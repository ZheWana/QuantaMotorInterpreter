import inspect
import threading
from enum import Enum
import queue
import serial
from serial import serialutil
import sys
import time


class State(Enum):
    INIT = 0
    ORDER = 1
    IN_WHILE = 2
    LOOP_OUT = 3
    COMPLETE = 4


class Interpreter:
    msgs = {
        # Errors
        "serial_err": "Wrong serial parameter!",
        "cmd_err": "Wrong command! Please check Usage!",
        "mstep_err": "Wrong mstep number! Please check input number!",
        "convert_err": "Catch Value error! Please check the numerical parameters!",
        "type_err": "Catch Type error! Please check the parameters!",
        "reply_err": "Wrong reply!",
        # Tips
        "exec_err": "Script execution Error, press any key to exit.",
        "exec_cpl": "Script execution complete, press any key to exit.",
        "check_cpl": "Script check complete, press any key to exit.",
        "ask_if_run": "Would you like to run the script or just check it out?\nY for run, n for check.\nInput(Y/n):"
    }

    cmd_list = ["ctrl", "echoff", "delay", "while", "endwhile", "e", "ab", "set"]

    mstep_list = ["200", "400", "800", "1600", "3200", "6400", "12800", "25600",
                  "1000", "2000", "4000", "5000", "8000", "10000", "20000", "25000"]

    def __init__(self):
        # 初始化定义实例变量
        self.ser = None
        self.state = State.INIT
        self.line_number = 0  # 记录当前行号
        self.line = ""
        self.loop_time = 0
        self.loop_body = ""  # 记录循环体代码
        self.file_path = sys.argv[1]

        # 共享资源保护锁
        self.data_lock = threading.Lock()

        # 轴向线程及其需要的消息队列、条件变量、条件标志位、运行标志位
        self.tx = None
        self.x_running = False
        self.x_cond_flag = True
        self.x_queue = queue.Queue()
        self.x_cond = threading.Condition(self.data_lock)

        self.ty = None
        self.y_running = False
        self.y_cond_flag = True
        self.y_queue = queue.Queue()
        self.y_cond = threading.Condition(self.data_lock)

        self.tz = None
        self.z_running = False
        self.z_cond_flag = True
        self.z_queue = queue.Queue()
        self.z_cond = threading.Condition(self.data_lock)

        input_data = input(self.msgs["ask_if_run"])
        if input_data[0].upper() == 'Y':
            self.exec_flag = 1
        else:
            self.exec_flag = 0

    def error_log(self, msg: str):
        with self.data_lock:
            print("[Error]: " + str(self.line_number) + ": " + self.msgs[msg])
        if self.exec_flag:
            input(self.msgs["exec_err"])
            # self.x_queue.put("stop")
            # self.y_queue.put("stop")
            # self.tx.join()
            # self.ty.join()
            # exit()

    def serial_log(self, msg: str):
        with self.data_lock:
            print("[Serial]: " + self.msgs[msg])

    def content_check(self, content_list: list, content: str):
        for cmd in content_list:
            if content == cmd:
                return True
        return False

    def check_output(self, command: str):
        if self.content_check(self.cmd_list, command.split(" ")[0].strip().lower()):
            with self.data_lock:
                print("[Checking] " + str(self.line_number) + ": " + command)
                time.sleep(0.01)
        else:
            self.error_log("cmd_err")
        pass

    def serial_output(self, command: str):
        if inspect.stack()[1].function == "thread_main":
            self.waiting_for_axis()

        if self.content_check(self.cmd_list, command.split(" ")[0].strip().lower()):
            with self.data_lock:
                if self.exec_flag:
                    print("[Running] " + command)
                else:
                    print("[Checking] " + str(self.line_number) + ": " + command)
                time.sleep(0.01)
        else:
            self.error_log("cmd_err")
        pass

    def waiting_for_axis(self):
        with self.data_lock:
            while not (self.x_cond_flag and self.y_cond_flag and self.z_cond_flag):
                if not self.x_cond_flag:
                    self.x_cond.wait()
                if not self.y_cond_flag:
                    self.y_cond.wait()
                if not self.z_cond_flag:
                    self.z_cond.wait()

    def send_dual_axis(self, command: str):
        axis = command.split(" ")[1].lower()
        while True:
            if axis == "x" and self.x_cond_flag:
                with self.data_lock:
                    self.x_cond_flag = False
                    self.x_queue.put(command.strip())
                    break
            elif axis == "y" and self.y_cond_flag:
                with self.data_lock:
                    self.y_cond_flag = False
                    self.y_queue.put(command.strip())
                    break
            elif axis == "z" and self.z_cond_flag:
                with self.data_lock:
                    self.z_cond_flag = False
                    self.z_queue.put(command.strip())
                    break
            else:
                self.waiting_for_axis()

    def delay_sec(self, line: str):
        with self.data_lock:
            try:
                delay_time = float(line.split(' ')[1])
                print("Delay for " + str(time) + " seconds.")
                if self.exec_flag:
                    time.sleep(delay_time)
            except ValueError:
                self.error_log("convert_err")

    def thread_axis(self, axis: str, q: queue.Queue):
        axis_name = axis.strip().upper()
        while True:
            line = q.get()
            if line == "stop":
                exit()

            if type(line) == str:
                self.serial_output(line)
                time.sleep(0.01)

                time.sleep(1)
            else:
                self.error_log("type_err")

            with self.data_lock:
                if axis_name == "X":
                    self.x_cond_flag = True
                    self.x_cond.notify()
                elif axis_name == "Y":
                    self.y_cond_flag = True
                    self.y_cond.notify()
                elif axis_name == "Z":
                    self.z_cond_flag = True
                    self.z_cond.notify()

    def thread_main(self):
        with open(self.file_path, 'r') as f:
            while True:
                if self.state == State.INIT:
                    # Start the axis threads
                    self.tx = threading.Thread(target=self.thread_axis, args=("X", self.x_queue))
                    self.tx.start()

                    self.ty = threading.Thread(target=self.thread_axis, args=("Y", self.y_queue))
                    self.ty.start()

                    # try to read the file and open the serial port
                    line = f.readline()
                    self.line_number += 1
                    com = line.split(" ")[0].strip()
                    bound_rate = line.split(" ")[1].strip()
                    del line

                    try:
                        self.ser = serial.Serial(com, bound_rate)
                    except serialutil.SerialException:
                        self.error_log("serial_err")

                    self.state = State.ORDER
                    continue

                elif self.state == State.ORDER:
                    self.line = f.readline()
                    self.line_number += 1

                    if self.line == "\n":
                        continue
                    elif self.line == "":
                        self.state = State.COMPLETE
                        continue
                    cmd_head = self.line.split(" ")[0].strip()

                    # "ctrl", "echoff", "delay", "while", "endwhile", "e", "ab", "set"
                    if cmd_head == "ctrl":
                        self.send_dual_axis(self.line.strip())
                    elif cmd_head == "delay":
                        self.delay_sec(self.line.strip())
                    elif cmd_head == "echoff" or cmd_head == "ab" or cmd_head == "e":
                        self.serial_output(self.line.strip())
                    elif cmd_head == "set":
                        if self.content_check(self.mstep_list, self.line.split(" ")[2].strip()):
                            self.serial_output(self.line.strip())
                        else:
                            self.error_log("mstep_err")
                    elif cmd_head == "while":
                        try:
                            self.loop_time = int(self.line.split(" ")[1].strip())
                        except ValueError:
                            self.error_log("convert_err")
                        self.state = State.IN_WHILE
                        continue
                    else:
                        self.error_log("cmd_err")

                elif self.state == State.IN_WHILE:
                    self.line = f.readline()
                    self.line_number += 1

                    if self.line == "\n":
                        continue

                    if self.line.strip() == "endwhile":
                        if self.exec_flag == 1:
                            self.state = State.LOOP_OUT
                        else:
                            self.state = State.ORDER
                        continue
                    else:
                        if self.content_check(self.cmd_list, self.line.split(" ")[0]):
                            if self.exec_flag == 0:
                                self.check_output(self.line.strip())
                            self.loop_body += self.line
                        else:
                            self.error_log("cmd_err")

                elif self.state == State.LOOP_OUT:
                    body_len = len(self.loop_body.split("\n"))
                    for j in range(self.loop_time):
                        for i in range(0, body_len - 1):
                            line = self.loop_body.split("\n")[i]
                            cmd_head = line.strip().split(' ')[0].strip()
                            if cmd_head == "delay":
                                self.delay_sec(line)
                            elif cmd_head == "ctrl" or cmd_head == "zero":
                                self.send_dual_axis(line)
                            elif cmd_head == "echoff" or cmd_head == "ab" or cmd_head == "e":
                                self.serial_output(line)
                            elif cmd_head == "set":
                                if self.content_check(self.mstep_list, self.line.split(" ")[2].strip()):
                                    self.serial_output(self.line)
                                else:
                                    self.error_log("mstep_err")
                            else:
                                self.error_log("cmd_err")
                    self.loop_body = ""
                    self.state = State.ORDER

                elif self.state == State.COMPLETE:
                    try:
                        if self.ser is not None:
                            self.ser.close()
                    except NameError or AttributeError:
                        pass
                    if self.exec_flag:
                        input(self.msgs["exec_cpl"])
                        self.x_queue.put("stop")
                        self.y_queue.put("stop")
                        self.tx.join()
                        self.ty.join()
                        exit()
                    else:
                        input(self.msgs["check_cpl"])
                    pass


i = Interpreter()
t_main = threading.Thread(target=i.thread_main)
t_main.start()