import serial  
import json
import queue
import threading
import yaml
import os
import time
import glob
import numpy as np

curpath = os.path.realpath(__file__)
thisPath = os.path.dirname(curpath)
with open(thisPath + '/config.yaml', 'r') as yaml_file:
	f = yaml.safe_load(yaml_file)

MOTION_CMD_TYPES = (1, 111)
GAIT_ENGINE_CMD_TYPE = 117


def is_motion_command(data):
	"""True when a queued command actually moves the robot: T:1 speed or
	T:111 gait with a nonzero component, or any T:117 gait-engine command.
	A T:117 is a partial update — absent fields inherit the firmware's
	previous params, so even a height-only command can restart walking at
	an inherited speed; the host cannot know the merged state, and
	over-reporting motion to the failsafe heartbeat is the safe direction.
	A pure stop (all zeros) is not motion — it must not feed the
	failsafe heartbeat. Pure function."""
	if not isinstance(data, dict):
		return False
	if data.get('T') == GAIT_ENGINE_CMD_TYPE:
		return True
	if data.get('T') not in MOTION_CMD_TYPES:
		return False
	return bool(data.get('L') or data.get('R') or data.get('FB') or data.get('LR'))

class ReadLine:
	def __init__(self, s):
		self.buf = bytearray()
		self.s = s

		self.sensor_data = []
		self.sensor_list = []
		try:
			self.sensor_data_ser = serial.Serial(glob.glob('/dev/ttyUSB*')[0], 115200)
			print("/dev/ttyUSB* connected succeed")
		except Exception as e:
			print(f"[base_ctrl] sensor serial not available: {e}")
			self.sensor_data_ser = None
		self.sensor_data_max_len = 51

		try:
			self.lidar_ser = serial.Serial(glob.glob('/dev/ttyACM*')[0], 230400, timeout=1)
			print("/dev/ttyACM* connected succeed")
		except Exception as e:
			print(f"[base_ctrl] lidar serial not available: {e}")
			self.lidar_ser = None
		self.ANGLE_PER_FRAME = 12
		self.HEADER = 0x54
		self.lidar_angles = []
		self.lidar_distances = []
		self.lidar_angles_show = []
		self.lidar_distances_show = []
		self.last_start_angle = 0

	def readline(self):
		i = self.buf.find(b"\n")
		if i >= 0:
			r = self.buf[:i+1]
			self.buf = self.buf[i+1:]
			return r
		while True:
			i = max(1, min(512, self.s.in_waiting))
			data = self.s.read(i)
			i = data.find(b"\n")
			if i >= 0:
				r = self.buf + data[:i+1]
				self.buf[0:] = data[i+1:]
				return r
			else:
				self.buf.extend(data)

	def clear_buffer(self):
		self.s.reset_input_buffer()

	def read_sensor_data(self):
		if self.sensor_data_ser == None:
			return

		try:
			buffer_clear = False
			while self.sensor_data_ser.in_waiting > 0:
				buffer_clear = True
				sensor_readline = self.sensor_data_ser.readline()
				if len(sensor_readline) <= self.sensor_data_max_len:
					self.sensor_list.append(sensor_readline.decode('utf-8')[:-2])
				else:
					self.sensor_list.append(sensor_readline.decode('utf-8')[:self.sensor_data_max_len])
					self.sensor_list.append(sensor_readline.decode('utf-8')[self.sensor_data_max_len:-2])
			if buffer_clear:
				self.sensor_data = self.sensor_list.copy()
				self.sensor_list.clear()
				self.sensor_data_ser.reset_input_buffer()
		except Exception as e:
			print(f"[base_ctrl.read_sensor_data] error: {e}")

	def parse_lidar_frame(self, data):
		# header = data[0]
		# verlen = data[1]
		# speed  = data[3] << 8 | data[2]
		start_angle = (data[5] << 8 | data[4]) * 0.01
		# print(start)
		# end_angle = (data[43] << 8 | data[42]) * 0.01
		for i in range(0, self.ANGLE_PER_FRAME):
			offset = 6 + i * 3
			distance = data[offset+1] << 8 | data[offset]
			confidence = data[offset+2]
			# lidar_angles.append(np.radians(start_angle + i * 0.167))
			self.lidar_angles.append(np.radians(start_angle + i * 0.83333 + 180))
			# lidar_angles.append(np.radians(start_angle + end_angle))
			self.lidar_distances.append(distance)
		# end_angle = (data[43] << 8 | data[42]) * 0.01
		# timestamp = data[45] << 8 | data[44]
		# crc = data[46]
		return start_angle

	def lidar_data_recv(self):
		if self.lidar_ser == None:
			return
		try:
			while True:
				self.header = self.lidar_ser.read(1)
				if self.header == b'\x54':
					# Read the rest of the data
					data = self.header + self.lidar_ser.read(46)
					hex_data = [int(hex(byte), 16) for byte in data]
					start_angle = self.parse_lidar_frame(hex_data)
					if self.last_start_angle > start_angle:
						break
					self.last_start_angle = start_angle
				else:
					self.lidar_ser.flushInput()

			self.last_start_angle = start_angle
			self.lidar_angles_show = self.lidar_angles.copy()
			self.lidar_distances_show = self.lidar_distances.copy()
			self.lidar_angles.clear()
			self.lidar_distances.clear()
		except Exception as e:
			print(f"[base_ctrl.lidar_data_recv] error: {e}")
			self.lidar_ser = serial.Serial(glob.glob('/dev/ttyACM*')[0], 230400, timeout=1)


class _StubSerial:
	in_waiting = 0

	def close(self):
		pass


class ReadLineStub:
	"""Inert ReadLine stand-in for NullBaseController (no UART attached)."""

	def __init__(self):
		self.s = _StubSerial()
		self.buf = bytearray()
		self.sensor_data = []
		self.lidar_angles_show = []
		self.lidar_distances_show = []

	def readline(self):
		return b""

	def clear_buffer(self):
		self.buf = bytearray()

	def read_sensor_data(self):
		pass

	def lidar_data_recv(self):
		pass


class BaseController:

	def __init__(self, uart_dev_set, buad_set):
		self._uart = uart_dev_set
		self._baud = buad_set
		self.connected = True
		self.ser = serial.Serial(uart_dev_set, buad_set, timeout=1)
		self.rl = ReadLine(self.ser)
		self.command_queue = queue.Queue()
		self.command_thread = threading.Thread(target=self.process_commands, daemon=True)
		self.command_thread.start()
		self.motion_hook = None
		self.feedback_hook = None
		self._last_reconnect = 0

		self.data_buffer = None
		self.base_data = None

		self.use_lidar = f['base_config']['use_lidar']
		self.extra_sensor = f['base_config']['extra_sensor']


	def feedback_data(self):
		if not self.connected:
			return None
		try:
			while self.rl.s.in_waiting > 0:
				self.data_buffer = json.loads(self.rl.readline().decode('utf-8'))
				if 'T' in self.data_buffer:
					# print(self.data_buffer)
					self.base_data = self.data_buffer
					self.data_buffer = None
					self._notify_feedback(self.base_data)
					if self.base_data["T"] == 1003:
						print(self.base_data)
						return self.base_data
			self.rl.clear_buffer()
			self.data_buffer = json.loads(self.rl.readline().decode('utf-8'))
			self.base_data = self.data_buffer
			self._notify_feedback(self.base_data)
			return self.base_data
		except Exception as e:
			self.rl.clear_buffer()
			print(f"[base_ctrl.feedback_data] error: {e}")


	def on_data_received(self):
		self.ser.reset_input_buffer()
		data_read = json.loads(self.rl.readline().decode('utf-8'))
		return data_read


	def send_command(self, data):
		self.command_queue.put(data)


	def _try_reconnect(self):
		"""Reopen the UART if the last attempt is older than 5s (backoff)."""
		if time.time() - self._last_reconnect <= 5:
			return
		self._last_reconnect = time.time()
		try:
			self.ser = serial.Serial(self._uart, self._baud, timeout=1)
			self.rl = ReadLine(self.ser)
			self.connected = True
			print("[base_ctrl] serial reconnected")
		except Exception:
			pass


	def _notify_motion(self, data):
		if self.motion_hook is None:
			return
		try:
			if is_motion_command(data):
				self.motion_hook(time.time())
		except Exception:
			pass


	def _notify_feedback(self, data):
		"""Fire feedback_hook for every parsed telemetry/event line (T:-117
		gait completion, T:-207 battery, ...). Hook exceptions are absorbed:
		telemetry must never break the command pipeline."""
		if self.feedback_hook is None:
			return
		try:
			if isinstance(data, dict) and 'T' in data:
				self.feedback_hook(data)
		except Exception:
			pass


	def _process_one(self, data):
		"""Handle one dequeued command. While disconnected, motion commands
		still feed the hook (heartbeat tracking) but nothing is written."""
		if not self.connected:
			self._try_reconnect()
			self._notify_motion(data)
			return
		self.ser.write((json.dumps(data) + '\n').encode("utf-8"))
		self._notify_motion(data)


	def process_commands(self):
		while True:
			data = self.command_queue.get()
			self._process_one(data)


	def base_json_ctrl(self, input_json):
		self.send_command(input_json)


	def base_speed_ctrl(self, input_left, input_right):
		data = {"T":1,"L":input_left,"R":input_right}
		self.send_command(data)


	def gait_engine_ctrl(self, mode, speed, heading=None, height=None,
			stride=None, distance=None):
		"""T:117 parametric gait engine (crawl/walk/run). mode 0/1/2,
		speed 0..1; the rest are optional — None lets the firmware keep
		its current value (the command is a partial update). Speed is
		clamped per mode by the firmware (run caps at 0.8)."""
		data = {"T": GAIT_ENGINE_CMD_TYPE, "mode": int(mode), "speed": float(speed)}
		if heading is not None:
			data["heading"] = float(heading)
		if height is not None:
			data["height"] = float(height)
		if stride is not None:
			data["stride"] = float(stride)
		if distance is not None:
			data["distance"] = float(distance)
		self.send_command(data)


	def gimbal_ctrl(self, input_x, input_y, input_speed, input_acceleration):
		data = {"T":133,"X":input_x,"Y":input_y,"SPD":input_speed,"ACC":input_acceleration}
		self.send_command(data)


	def base_oled(self, input_line, input_text):
		# data = {"T":3,"lineNum":input_line,"Text":input_text}
		data = {"T":202,"line":input_line + 1,"text":input_text,"update":1}
		self.send_command(data)


	def rgb_light(self, id, r, g, b):
		data = {"T":201,"set":[id, r, g, b]}
		self.send_command(data)

	def gimbal_dev_close(self):
		self.ser.close()


if __name__ == '__main__':
	# RPi5
	# base = BaseController('/dev/ttyAMA0', 115200)

	# RPi4B
	base = BaseController('/dev/serial0', 115200)

	# base.send_command({"T":201,"set":[0,9,0,0]})
	# time.sleep(1)
	# base.send_command({"T":201,"set":[1,9,0,0]})
	# time.sleep(1)

	# breath light for 15s
	# base.breath_light(15)

	# gimble ctrl, look forward
	#                x  y  spd acc
	# base.gimbal_ctrl(0, 0, 10, 0)
    
    # x(-180 ~ 180)
	# x- look left
	# x+ look right

	# y(-30 ~ 90)
	# y- look down
	# y+ look up

	while True:
		print(base.feedback_data())

class NullBaseController:
    """Drop-in stand-in when no ESP32 is attached (or --no-base): absorbs all
    commands, reports no feedback. Lets the UI and all services run headless."""

    def __init__(self):
        self.connected = False
        self.motion_hook = None
        self.feedback_hook = None
        self.base_data = {}
        self.rl = ReadLineStub()

    def send_command(self, data):
        pass

    def base_json_ctrl(self, input_json):
        pass

    def base_speed_ctrl(self, input_left, input_right):
        pass

    def gait_engine_ctrl(self, mode, speed, heading=None, height=None,
                         stride=None, distance=None):
        pass

    def gimbal_ctrl(self, input_x, input_y, input_speed, input_acceleration):
        pass

    def base_oled(self, input_line, input_text):
        pass

    def rgb_light(self, id, r, g, b):
        pass

    def breath_light(self, input_time):
        pass

    def feedback_data(self):
        return None

    def gimbal_dev_close(self):
        pass
