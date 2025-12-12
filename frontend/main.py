import sys
import csv
import json
import requests
import pyqtgraph as pg
import serial.tools.list_ports
import serial
import edgeimpulse as ei
import io
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QFileDialog
from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import QTimer
from project_gui import Ui_MainWindow
from pyqtgraph import mkPen
from influxdb_client import InfluxDBClient, Point, WriteOptions
from datetime import datetime

BACKEND_URL = "http://127.0.0.1:8000"

# --- InfluxDB Configuration ---
INFLUX_URL = "https://us-east-1-1.aws.cloud2.influxdata.com"
INFLUX_TOKEN = "f6N6NOB-obnghVNTReMRLWTEUtpjNkpOmH2_752W3_enJuV_vG4UiZzJ0BQXYj79motuOly0mY9wXA7HzEa4qQ=="
INFLUX_ORG = "evanjavier85@gmail.com"
INFLUX_BUCKET = "Coklat"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # GUI Theme    
        self.apply_stylesheet()

        self.serial_port = None
        self.serial_buffer = ""

        # Initial UI Setup
        self.ui.comboBoxPort.setEnabled(True)
        self.ui.btnConnect.setText("Connect")
        self.refresh_ports()

        # InfluxDB Setup
        try:
            self.influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            self.write_api = self.influx_client.write_api(write_options=WriteOptions(batch_size=10000, flush_interval=250))
            print("InfluxDB Client setup complete.")
        except Exception as e:
            print(f"Error setup InfluxDB: {e}")
            self.write_api = None

        self.num_sensors = 7

        # Sensor Order
        self.sensor_names = [
            "NO2_multi",      # Index 0
            "C2H5OH_multi",   # Index 1
            "VOC_multi",      # Index 2
            "CO_multi",       # Index 3
            "CO_mics",        # Index 4
            "C2H5OH_mics",    # Index 5
            "VOC_mics"        # Index 6
        ]

        # Label UI Mapping
        self.sensor_label_order = [
            self.ui.Value_NO2_Multi,    # Index 0
            self.ui.Value_C2H5OH_Multi, # Index 1
            self.ui.Value_VOC_Multi,    # Index 2
            self.ui.Value_CO_Multi,     # Index 3
            self.ui.Value_CO_mics,      # Index 4
            self.ui.Value_C2H5OH_mics,  # Index 5
            self.ui.Value_VOC_mics      # Index 6
        ]

        # Checkbox Mapping
        self.checkboxes = [
            self.ui.checkBox_7,  # NO2 (Multi) - Index 0
            self.ui.checkBox_9,  # C2H5OH (Multi) - Index 1
            self.ui.checkBox_8,  # VOC (Multi) - Index 2
            self.ui.checkBox_6,  # CO (Multi) - Index 3
            self.ui.checkBox,    # CO (MICS) - Index 4
            self.ui.checkBox_3,  # C2H5OH (MICS) - Index 5
            self.ui.checkBox_2   # VOC (MICS) - Index 6
        ]

        self.visible_signals = [True for _ in range(self.num_sensors)]

        # Graph and Checkbox Colour
        self.sensor_color_codes = [
            '#00ffff',  # Cyan (NO2)
            '#8a2be2',  # Purple (Eth Multi)
            '#964b00',  # Brown (VOC Multi)
            '#ffff00',  # Yellow (CO Multi)
            '#ff0000',  # Red (CO Mics)
            '#0000ff',  # Blue (Eth Mics)
            '#00ff00',  # Green (VOC Mics)
        ]
        self.sensor_colors = [mkPen(color, width=2) for color in self.sensor_color_codes]

        # Checkbox Setup
        for i, cb in enumerate(self.checkboxes):
            cb.setChecked(True)
            color = self.sensor_color_codes[i]
            cb.setStyleSheet(f"color: {color}; font-weight: bold;")
            cb.stateChanged.connect(lambda state, idx=i: self.toggle_signal_visibility(idx, state))

        self.data_buffer = [[] for _ in range(self.num_sensors)]
        self.time_buffer = []
        self.ei_buffer = []
        self.max_points_display = 1500

        # Graph Setup
        self.graphData = []
        self.ui.plotWidget.setBackground('#f0f0f0')
        self.ui.plotWidget.setLabel('bottom', 'Time', units='s')
        self.ui.plotWidget.setLabel('left', 'Value', units='V/ppm')
        self.ui.plotWidget.showGrid(x=True, y=True)
        self.plot = self.ui.plotWidget.plot(pen='b')

        self.timer = QTimer()
        self.connected = False
        self.ui.btnStart.setEnabled(False)

        # Signals
        self.ui.btnConnect.clicked.connect(self.connect_backend)
        self.ui.btnDisconnect.clicked.connect(self.disconnect_backend)
        self.ui.btnStart.clicked.connect(self.start_sampling)
        self.ui.btnStop.clicked.connect(self.stop_sampling)
        self.ui.btnExportCSV.clicked.connect(self.export_csv)
        self.ui.btnExportJSON.clicked.connect(self.export_json)
        self.ui.btnExportEI.clicked.connect(self.export_edge_impulse)
        self.ui.btnReset.clicked.connect(self.reset_data)

        self.ui.sampleName.setPlaceholderText("Enter sample's name")
        self.current_sample_name = "unnamed_sample"

        self.timer.timeout.connect(self.fetch_data)

    # Theme Function
    def apply_stylesheet(self):
        style = """
        QMainWindow { background-color: #1e1e1e; color: #e0e0e0; }
        QGroupBox { border: 2px solid #3e3e3e; border-radius: 6px; margin-top: 1.2em; padding-top: 10px; font-weight: bold; color: #4db8ff; background-color: #252526; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; left: 10px; }
        QPushButton { background-color: #007acc; color: white; border-radius: 4px; padding: 6px; font-weight: bold; border: none; }
        QPushButton:hover { background-color: #0062a3; }
        QPushButton#btnStop { background-color: #c42b1c; }
        QPushButton#btnStop:hover { background-color: #a02014; }
        QPushButton#btnReset { background-color: #d19a02; color: black; }
        QPushButton#btnReset:hover { background-color: #b58502; }
        QLineEdit, QComboBox { background-color: #333333; color: #ffffff; border: 1px solid #555; border-radius: 4px; padding: 4px; }
        QLabel { color: #cccccc; }
        /* Checkbox default style (warna spesifik di-override oleh kode Python) */
        QCheckBox { spacing: 5px; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        """
        self.setStyleSheet(style)

    def toggle_signal_visibility(self, index, state):
        self.visible_signals[index] = bool(state)

    def refresh_ports(self):
        self.ui.comboBoxPort.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.ui.comboBoxPort.addItem(p.device)
    
    def connect_backend(self):
        selected_port = self.ui.comboBoxPort.currentText().strip()
        if not selected_port: return
        try:
            response = requests.post(f"{BACKEND_URL}/connect", json={"port": selected_port})
            if response.status_code == 200:
                self.connected = True
                self.ui.lblStatusSerial.setText(f"Status: Connected {selected_port} ✅")
                self.ui.lblStatusSerial.setStyleSheet("color: #4cd964; font-weight: bold;")
                self.ui.btnStart.setEnabled(True)
                self.ui.btnStop.setEnabled(True)
                QMessageBox.information(self, "Success", "Connected!")
            else: raise Exception(response.text)
        except Exception as e:
            self.ui.lblStatusSerial.setText("Status: Not Connected ❌")
            QMessageBox.warning(self, "Error", str(e))
    
    def disconnect_backend(self):
        try:
            requests.post(f"{BACKEND_URL}/disconnect")
            self.connected = False
            self.timer.stop()
            self.ui.btnStart.setEnabled(False)
            self.ui.btnStop.setEnabled(False)
            self.ui.lblStatusSerial.setText("Status: Disconnected ❌")
            self.ui.lblStatusSerial.setStyleSheet("color: #ff5f56;")
        except: pass

    def start_sampling(self):
        name_input = self.ui.sampleName.text().strip()
        if not name_input:
            QMessageBox.warning(self, "Warning", "Isi Nama Sample!")
            return
        self.current_sample_name = name_input
        self.ui.sampleName.setEnabled(False)
        try:
            requests.post(f"{BACKEND_URL}/start_sampling")
            self.ui.lblStatusSampling.setText("Status: Sampling ⏳")
            self.ui.lblStatusSampling.setStyleSheet("color: #ffcc00; font-weight: bold;")
            self.ui.btnStop.setEnabled(True)
            self.ui.btnStart.setEnabled(False)
            self.ui.btnReset.setEnabled(False)
            self.timer.start(250) # 250ms
        except: pass

    def stop_sampling(self):
        try:
            requests.post(f"{BACKEND_URL}/stop_sampling")
            self.ui.lblStatusSampling.setText("Status: Stopped ⛔")
            self.ui.lblStatusSampling.setStyleSheet("color: #ff5f56; font-weight: bold;")
            self.ui.btnStop.setEnabled(False)
            self.ui.btnStart.setEnabled(True)
            self.ui.btnReset.setEnabled(True)
            self.ui.sampleName.setEnabled(True)
            self.timer.stop()
        except: pass

    def reset_data(self):
        if self.timer.isActive(): return
        reply = QMessageBox.question(self, 'Reset', "Hapus data?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.data_buffer = [[] for _ in range(self.num_sensors)]
            self.time_buffer = [] # Time reset
            self.ei_buffer = []
            self.ui.plotWidget.clear()
            for lbl in self.sensor_label_order:
                lbl.setText("0.000")

            try:
                requests.post(f"{BACKEND_URL}/reset")
                print("Data reset di Backend")
            except Exception as e:
                print(f"Gagal reset backend: {e}")

    def fetch_data(self):
        try:
            response = requests.get(f"{BACKEND_URL}/data")
            if response.status_code == 200:
                json_resp = response.json()
                values = json_resp.get("values", [])
                
                if len(values) >= self.num_sensors:
                    # Time update
                    current_time_sec = len(self.ei_buffer) * 0.250
                    self.time_buffer.append(current_time_sec)
                    if len(self.time_buffer) > self.max_points_display:
                        self.time_buffer.pop(0)

                    # Buffer Update
                    for i in range(self.num_sensors):
                        self.data_buffer[i].append(values[i])
                        if len(self.data_buffer[i]) > self.max_points_display:
                            self.data_buffer[i].pop(0)
                    
                    # EI Buffer Update
                    self.ei_buffer.append({
                        "timestamp": current_time_sec * 1000, # ms 
                        "values": values[:self.num_sensors]
                    })

                    # Number Labels Update
                    for i, val in enumerate(values[:self.num_sensors]):
                        self.sensor_label_order[i].setText(f"{val:.3f}")

                    # Graph Update
                    self.ui.plotWidget.clear()
                    for i in range(self.num_sensors):
                        if len(self.data_buffer[i]) > 0 and self.visible_signals[i]:
                            limit = min(len(self.time_buffer), len(self.data_buffer[i]))
                            self.ui.plotWidget.plot(
                                self.time_buffer[:limit], 
                                self.data_buffer[i][:limit], 
                                pen=self.sensor_colors[i], 
                                name=self.sensor_names[i]
                            )

                    # Send to InfluxDB
                    if self.write_api:
                        p = Point("Coklat").tag("sample_name", self.current_sample_name).time(datetime.utcnow())
                        for i, v in enumerate(values[:self.num_sensors]): p.field(self.sensor_names[i], float(v))
                        self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
        except: pass

    def get_backend_history(self):
        """ Mengambil seluruh data yang tersimpan di RAM Rust """
        try:
            response = requests.get(f"{BACKEND_URL}/history")
            if response.status_code == 200:
                data = response.json().get("data", []) # Format: [[val1, val2...], [val1, val2...]]
                return data
            else:
                return []
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal mengambil history dari backend: {e}")
            return []

    def export_csv(self):
        history_data = self.get_backend_history()
        if not history_data:
            QMessageBox.warning(self, "Warning", "Tidak ada data di backend untuk diexport!")
            return

        name = self.ui.sampleName.text().strip()
        if name == "": name = "unnamed"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV", f"{name}.csv", "CSV Files (*.csv)")
        
        if not file_path: return
        
        try:
            with open(file_path, 'w', newline='') as f:
                w = csv.writer(f, delimiter=';')
                
                # Header: timestamp, sensors...
                header = ["timestamp"] + self.sensor_names
                w.writerow(header)
                
                # Rows: timestamp, values...
                for idx, row in enumerate(history_data):
                    ts = idx * 250
                    w.writerow([ts] + row)
                    
            QMessageBox.information(self, "Export", f"Berhasil export {len(history_data)} baris data!")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def export_json(self):
        history_data = self.get_backend_history()
        if not history_data:
            QMessageBox.warning(self, "Warning", "Tidak ada data di backend!")
            return

        name = self.ui.sampleName.text().strip()
        if name == "": name = "unnamed"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save JSON", f"{name}.json", "JSON Files (*.json)")
        
        if not file_path: return

        # Data Transpose
        try:
            # history_data = [[s1, s2...], [s1, s2...]]
            # transposed = [[s1, s1...], [s2, s2...]]
            transposed = list(zip(*history_data))
            
            signals = []
            for i, sensor_name in enumerate(self.sensor_names):
                if i < len(transposed):
                    signals.append({
                        "name": sensor_name,
                        "data": transposed[i]
                    })
            
            with open(file_path, 'w') as f:
                json.dump({"signals": signals}, f, indent=4)
                
            QMessageBox.information(self, "Export", "Berhasil export JSON!")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def export_edge_impulse(self):
        history_data = self.get_backend_history()
        if not history_data:
            QMessageBox.warning(self, "Warning", "Tidak ada data di backend!")
            return

        out = io.StringIO()
        w = csv.writer(out)
        
        # Edge Impulse Header
        w.writerow(["timestamp"] + self.sensor_names)
        
        for idx, row in enumerate(history_data):
            ts = idx * 250
            w.writerow([ts] + row)
            
        self.upload_to_edge_impulse(out.getvalue().encode("utf-8"), f"{self.ui.sampleName.text()}.csv")

    def upload_to_edge_impulse(self, file_bytes, filename):
        EI_KEY = "ei_c249c4151d9981c84a87e9f7d35ca4a76aec3a7e0888c4c2"
        url = "https://ingestion.edgeimpulse.com/api/training/files"
        try:
            r = requests.post(url, headers={"x-api-key": EI_KEY, "x-label": filename.replace(".csv",""), "x-file-name": filename}, files={'data': (filename, file_bytes, 'text/csv')})
            if r.status_code == 200: QMessageBox.information(self, "Success", "Uploaded!")
            else: QMessageBox.warning(self, "Failed", r.text)
        except Exception as e: QMessageBox.critical(self, "Error", str(e))

    def closeEvent(self, e):
        if hasattr(self, 'write_api') and self.write_api: self.write_api.close(); self.influx_client.close()
        e.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())