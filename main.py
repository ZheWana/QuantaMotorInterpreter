import os

from serial import serialutil
from enum import Enum
import threading
import inspect
import serial
import queue
import time
import sys


class State(Enum):
    INIT = 0
    ORDER = 1
    IN_WHILE = 2
    LOOP_OUT = 3
    COMPLETE = 4


class Interpreter:
    msgs = {
        # Errors
        "serial_err": "Wrong serial parameter! Press Enter to force continue.",
        "cmd_err": "Wrong command! Please check Usage!",
        "axis_err": "Wrong command axis! Please check Usage!",
        "para_err": "Wrong number of parameters! Please check Usage!",
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

    cmd_list = ["ctrl", "echoff", "delay", "while", "endwhile", "e", "ab", "set", "join", "zero"]

    mstep_list = ["200", "400", "800", "1600", "3200", "6400", "12800", "25600",
                  "1000", "2000", "4000", "5000", "8000", "10000", "20000", "25000"]

    def __init__(self):
        # 初始化定义实例变量
        self.f = None
        self.ser = None
        self.line = ""
        self.loop_time = 0
        self.loop_body = ""  # 记录循环体代码
        self.file_path = sys.argv[1]

        # 共享资源保护锁
        self.data_lock = threading.Lock()

        # 轴向线程及其需要的消息队列、条件变量、条件标志位、运行标志位
        self.t_in = None

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

    def serial_input(self, axis: str, msg=None):
        with self.data_lock:
            if msg is not None:
                print(msg)
            if self.t_in is None:
                self.t_in = threading.Thread(target=self.thread_input)
                self.t_in.start()

        if axis == "X":
            return self.x_queue.get()
        elif axis == "Y":
            return self.y_queue.get()

    def error_log(self, msg: str, line_number: int):
        if inspect.stack()[1].function == "thread_main":
            self.waiting_for_axis()

        with self.data_lock:
            print("[Error]: " + str(line_number) + ": " + self.msgs[msg])
        if self.exec_flag:
            input()
            if msg != "serial_err":
                self.x_queue.put("stop")
                self.y_queue.put("stop")
                self.tx.join()
                self.ty.join()
                exit()

    @staticmethod
    def content_check(content_list: list, content: str):
        for cmd in content_list:
            if content == cmd:
                return True
        return False

    def serial_output(self, command: str, line_number: int):
        if self.content_check(self.cmd_list, command.split(" ")[0].strip().lower()):
            with self.data_lock:
                if self.exec_flag:
                    print("[Running] " + command)
                    if self.ser is not None:
                        self.ser.write(command.strip().encode() + "\n".encode())
                else:
                    print("[Checking] " + command)
            time.sleep(0.01)
        else:
            self.error_log("cmd_err", line_number)
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

    def send_dual_axis(self, command: str, line_number: int):
        axis = command.split(" ")[1].lower()
        while True:
            if axis == "x" and self.x_cond_flag:
                with self.data_lock:
                    self.x_cond_flag = False
                    self.x_queue.put(str(line_number) + ":" + command.strip())
                    break
            elif axis == "y" and self.y_cond_flag:
                with self.data_lock:
                    self.y_cond_flag = False
                    self.y_queue.put(str(line_number) + ":" + command.strip())
                    break
            elif axis == "z" and self.z_cond_flag:
                with self.data_lock:
                    self.z_cond_flag = False
                    self.z_queue.put(str(line_number) + ":" + command.strip())
                    break
            else:
                self.waiting_for_axis()

    def thread_input(self):
        if self.ser is None:
            reply = input()
        else:
            reply = self.ser.readline()

        self.x_queue.put(reply)
        self.y_queue.put(reply)

        self.t_in = None

    def thread_axis(self, axis: str, q: queue.Queue):
        axis_name = axis.strip().upper()
        while True:
            output_flag = 0

            line = q.get()

            if line == "stop":
                exit()
                continue
            elif line == "Xstop" or line == "Ystop" \
                    or line == "Xzero" or line == "Yzero":
                continue
            else:
                try:
                    line_number = int(line.split(":")[0])
                    line = line.split(":")[1]
                except:
                    continue

            if type(line) == str:
                self.serial_output(line, line_number)
                output_flag = 1
            else:
                self.error_log("type_err", line_number)

            # 等待线程接收运行结果
            if self.exec_flag:
                while output_flag == 1:
                    reply = self.serial_input(axis)
                    if line.split(" ")[0] == "ctrl":
                        if reply == axis + "stop":
                            print("......OK")
                            output_flag = 0
                            break
                    elif line.split(" ")[0] == "zero":
                        if reply == axis + "zero":
                            print("......OK")
                            output_flag = 0
                            break

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

    def parse_ctrl(self, line: str, line_number: int):
        length = len(line.split(" "))

        if length != 4:
            self.error_log("para_err", line_number)
            return

        self.send_dual_axis(line, line_number)
        pass

    def parse_echoff(self, line: str, line_number: int):
        length = len(line.split(" "))

        if length != 1:
            self.error_log("para_err", line_number)
            return

        self.serial_output(line, line_number)
        pass

    def parse_delay(self, line: str, line_number: int):
        length = len(line.split(" "))

        if length != 2:
            self.error_log("para_err", line_number)
            return

        with self.data_lock:
            try:
                delay_time = float(line.split(' ')[1])
                if self.exec_flag:
                    print("[Running]" + line)
                    time.sleep(delay_time)
                else:
                    print("[Checking]" + line)
            except ValueError:
                self.error_log("convert_err", line_number)
        pass

    def parse_while(self, line: str, line_number: int):
        length = len(line.split(" "))

        if length != 2:
            self.error_log("para_err", line_number)
            return

        try:
            local_loop_time = int(line.split(" ")[1])
        except ValueError:
            self.error_log("convert_err", line_number)
            return

        local_line_number = line_number

        for i in range(local_loop_time):
            local_line = self.f.readline()
            local_line_number += 1
            if line.strip() == "endwhile":
                return local_line_number
            else:
                self.parse(local_line, local_line_number)

    def parse_e(self, line: str, line_number: int):
        self.waiting_for_axis()
        self.serial_output(line, line_number)
        pass

    def parse_ab(self, line: str, line_number: int):
        length = len(line.split(" "))
        if length == 2:
            axis = line.split(" ")[1]
        else:
            self.error_log("cmd_err", line_number)
            return
        if axis.lower() != "x" or axis.lower() != "y":
            self.error_log("axis_err", line_number)
            return

        self.waiting_for_axis()
        self.serial_output(line, line_number)

    def parse_set(self, line: str, line_number: int):
        length = len(line.split(" "))
        if length == 3:
            axis = line.split(" ")[1]
            if not self.content_check(self.mstep_list, line.split(" ")[2].strip()):
                self.error_log("mstep_err", line_number)
                return
        else:
            self.error_log("para_err", line_number)
            return
        if not (axis.lower() == "x" or axis.lower() == "y"):
            self.error_log("axis_err", line_number)

        self.waiting_for_axis()
        self.serial_output(line, line_number)

    def parse_join(self, line: str, line_number: int):
        length = len(line.split(" "))

        if length != 1:
            self.error_log("para_err", line_number)
            return

        self.serial_output(line, line_number)
        self.waiting_for_axis()
        pass

    def parse_zero(self, line: str, line_number: int):
        length = len(line.split(" "))

        if length != 2:
            self.error_log("para_err", line_number)
            return

        self.send_dual_axis(line, line_number)
        pass

    def parse(self, line: str, line_number: int):
        line = line.strip()
        cmd_head = line.split(" ")[0]
        match cmd_head:
            case "ctrl":
                self.parse_ctrl(line, line_number)
            case "echoff":
                self.parse_echoff(line, line_number)
            case "delay":
                self.parse_delay(line, line_number)
            case "join":
                self.parse_join(line, line_number)
            case "while":
                self.parse_while(line, line_number)
            case "e":
                self.parse_e(line, line_number)
            case "zero":
                self.parse_zero(line, line_number)
            case "ab":
                self.parse_ab(line, line_number)
            case "set":
                self.parse_set(line, line_number)
            case "endwhile":
                pass
            case _:
                self.error_log("cmd_err", line_number)

    def thread_main(self):
        # Start the axis threads
        self.tx = threading.Thread(target=self.thread_axis, args=("X", self.x_queue))
        self.tx.start()

        self.ty = threading.Thread(target=self.thread_axis, args=("Y", self.y_queue))
        self.ty.start()

        line_number = 1
        with open(self.file_path, "r") as self.f:
            line = self.f.readline()
            if len(line.split(" ")) != 3:
                self.error_log("para_err", line_number)

            try:
                self.ser = serial.Serial(line.split(" ")[0], line.split(" ")[1])
            except serialutil.SerialException:
                self.error_log("serial_err", line_number)

            while line != "":
                line = self.f.readline()
                line_number += 1
                if line.strip() == "":
                    continue
                self.parse(line, line_number)

        self.x_queue.put("stop")
        self.y_queue.put("stop")
        self.tx.join()
        self.ty.join()
        print(self.msgs["exec_cpl"])
        os.system("pause")
        exit()


instance = Interpreter()
instance.thread_main()
