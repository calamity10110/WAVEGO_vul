import subprocess
import time

import cv2
import numpy as np

try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
except ImportError:
    Picamera2 = None
    H264Encoder = None

try:
    import depthai as dai
except ImportError:
    dai = None


def usb_camera_detection():
    try:
        lsusb_output = subprocess.check_output(["lsusb"]).decode("utf-8")
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        print("USB Camera detection unavailable")
        return False
    if "Camera" in lsusb_output:
        print("USB Camera connected")
        return True
    print("USB Camera not connected")
    return False


class CameraRack:
    def __init__(self, width, height):
        self.usb_camera_connected = usb_camera_detection()
        self.csi_camera_connected = False
        self.oak_camera_connected = False
        self.camera = None

        if self.usb_camera_connected:
            self.camera = cv2.VideoCapture(0)
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        if not self.usb_camera_connected:
            print("init csi camera.")
            try:
                self.encoder = H264Encoder(1000000)
                self.picam2 = Picamera2()
                self.picam2.configure(self.picam2.create_video_configuration(main={"format": 'XRGB8888', "size": (width, height)}))
                self.picam2.start()
                self.csi_camera_connected = True
            except Exception:
                self.csi_camera_connected = False

        if not self.usb_camera_connected and not self.csi_camera_connected:
            try:
                self.pipeline = dai.Pipeline()

                self.camRgb = self.pipeline.createColorCamera()
                self.camRgb.setBoardSocket(dai.CameraBoardSocket.RGB)
                self.camRgb.setInterleaved(False)
                self.camRgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_720_P)

                self.xout = self.pipeline.createXLinkOut()
                self.xout.setStreamName("video")
                self.camRgb.video.link(self.xout.input)

                self.device = dai.Device(self.pipeline)
                self.output_queue = self.device.getOutputQueue(name="video", maxSize=8, blocking=False)

                self.oak_camera_connected = True
            except Exception as e:
                print(f"[cv_camera.CameraRack] error: {e}")
                self.oak_camera_connected = False

    def read(self, quality):
        try:
            if self.usb_camera_connected:
                success, input_frame = self.camera.read()
                if not success:
                    self.camera.release()
                    time.sleep(1)
                    self.camera = cv2.VideoCapture(0)
            elif self.csi_camera_connected:
                input_frame = self.picam2.capture_array()
            elif self.oak_camera_connected:
                input_frame = self.output_queue.get().getCvFrame()
                input_frame = cv2.resize(input_frame, (640, 480))
            else:
                input_frame = self._placeholder("camera read failed... \nusb - csi - oak", quality)
                time.sleep(1)
                return None, input_frame
            return input_frame, None
        except Exception as e:
            print(f"[cv_camera.CameraRack] error: {e}")
            return None, self._placeholder(f"camera read failed... \n{e}", quality)

    def _placeholder(self, message, quality):
        frame = 255 * np.ones((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, message,
                    (round(0.05 * 640), round(0.1 * 640 + 5 * 13)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.369, (0, 0, 0), 1)
        _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buffer.tobytes()
