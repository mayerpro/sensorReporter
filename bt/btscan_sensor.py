# Copyright 2020 Richard Koshak
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import struct
import traceback
import bluetooth
import bluetooth._bluetooth as bt
from core.sensor import Sensor

class SimpleBtSensor(Sensor):

    def __init__(self, publishers, params):
        super().__init__(publishers, params)

        self.address = params("Address")
        self.destination = params("Destination")
        if self.poll <= 25:
            raise ValueError("Poll must be less than 25")

        self.log.info("Configured simple BT sensor for %s, publishing to %s", self.address, self.destination)
        self.state  = None

    def check_state(self):
        result = bluetooth.lookup_name(self.address, timeout=25)
        self.log.debug("Scanned for %s, result = %s", self.address, result)
        value = "OFF" if result is None else "ON"
        if value != self.state:
            self.state = value
            self.publish_state()

    def publish_state(self):
        self._send(self.state, self.destination)

class BtRssiSensor(Sensor):

    def __init__(self, publishers, params):
        super().__init__(publishers, params)

        self.address = params("Address")
        self.destination = params("Destination")
        self.max_count = int(params("Max"))
        self.max_near = int(params("Near"))
        self.max_far = int(params("Far"))

        # Default the state to OFF/far away.
        self.state = "OFF"
        self.near_count = 0
        self.far_count = 0

        if self.poll <= 10:
            raise ValueError("Poll must be greater than 10 seconds.")

    def read_inquiry_mode(self, sock):
        self.log.debug("Saving the old filter")
        # Save the current filter
        old_filter = sock.getsockopt(bt.SOL_HCI, bt.HCI_FILTER, 14)

        self.log.debug("Creating filter for reading inquiry")
        # Setup the filter to receive only events related to the
        # read_inquirey_mode command.
        flt = bt.hci_filter_new()
        opcode = bt.cmd_opcode_pack(bt.OGF_HOST_CTL, bt.OCF_READ_INQUIRY_MODE)
        bt.hci_filter-set-ptype(flt, bt.HCI_EVENT_PKT)
        bt.hci_filter_set_event(flt, bt.EVT_CMD_COMPLETE)
        bt.hci_filter_set_opcode(flt, opcode)
        sock.setsockopt(bt.SOL_HCI, bt.HCI_FILTER, flt)

        self.log.debug("Reading the mode")
        # First read the current inquirey mode.
        bt.hci_send_cmd(sock, bluez.OGF_HOST_CTL, bt.OCF_READ_INQUIRY_MODE)

        pkt = sock.recv(255)
        status, mode = struct.unpack("xxxxxxBB", pkt)

        self.log.debug("Restoring the old filter")
        # Restore the old filter
        sock.setsockopt(bt.SOL_HCI, bt.HCI_FILTER, old_filter)

        return mode

    def write_inquiry_mode(self, sock, mode):
        old_filter = sock.getsockopt(bt.SOL_HCI, bt.HCI_FILTER, 14)

        # Setup socket filter to receive only events related to the
        # write_inquire_mode command
        flt = bt.hci_filter_new()
        opcode = bt.cmd_opcode_pack(bt.OGF_HOST_CTL, bt.OCF_WRITE_INQUIRY_MODE)
        bt.hci_filter_set_ptype(flt, bt.HCI_EVENT_PKT)
        bt.hci_filter_set_event(flt, bt.EVT_CMD_COMPLETE)
        bt.hci_filter_set_opcode(flt, opcode)
        sock.setsockopt(bt.SOL_HCI, bt.HCI_FILTER, flt)

        # Send the command
        bt.hci_send_cmd(sock, bt.OGF_HOST_CTL, bt.OCF_WRITE_INQUIRY_MODE,
                        struct.pack("B", mode))

        pkt = sock.recv(255)
        status = struct.unpack("xxxxxxB", pkt)[0]

        # Restore the old filter
        sock.setsockopt(bt.SOL_HCI, bt.HCI_FILTER, old_filter)

        return 0 if status else -1

    def device_inquiry_with_rssi(self, sock):
        # save the old filter
        old_filter = sock.getsockopt(bt.SOL_HCI, bt.HCI_FILTER, 14)

        # Perform a device inquiry on bluetooth device. The inquiry should last
        # 8 * 1.28 = 10.24 seconds before the inquiry is performed, bluez should
        # flush it's cache of previously discovered devices.
        flt = bt.hci_filter_new()
        bt.hci_filter_all_events(flt)
        bt.hci_filter_set_ptype(flt, bt.HCI_EVENT_PKT)
        sock.setsockopt(bt.SOL_HCI, bt.HCI_FILTER, flt)

        duration = 4
        max_responses = 255
        cmd_pkt = struct.pack("BBBBB", 0x33, 0x8b, 0x9e, duration, max_responses)
        bt.hci_send_cmd(sock, bt.OGF_LINK_CTL, bt.OCF_INQUIRY, cmd_pkt)

        results = []

        while True:
            pkt = sock.recv(255)
            ptype, event, plen = struct.unpack("BBB", pkt[:3])
            self.log.debug("Event: {}".format(event))
            if event == bt.EVT_INQUIRY_RESULT_WITH_RSSI:
                pkt = pkt[3:]
                nrsp = bt.get_byte(pkt[0])
                for i in range(nrsp):
                    addr = bt.ba2str(pkt[1+6*i:1+6*i+6])
                    rssi = bt.byte_to_signed_int(bt.get_byte(pkt[1 + 13 * nrsp + 1]))
                    self.log.debug("RSSI %s for %s", rssi, addr)
                    results.append((addr, rssi))
            elif event == bt.EVT_INQUIRY_COMPLETE:
                break
            elif event == bt.EVT_CMD_STATUS:
                status, ncmd, opcode = struct.unpack("BBH", pkt[3:7])
                if status:
                    self.warning("Something went wrong")
                    break
            elif event == bt.EVT_INQUIRY_RESULT:
                pkt = pkt[3:]
                nrsp = bt.get_byte(pkt[0])
                for i in range(nrsp):
                    addr = bt.ba2str(pkt[1+6*1:1+6*i+6])
                    self.log.info("Result without rssi from %s", addr)
                    results.append((addr, -1))
            else:
                self.debug("Unrecognized packet type")

        sock.setsockopt(bt.SOL_HCI, bt.HCI_FILTER, old_filter)
        return results

    def get_rssi(self):
        # Open the HCI socket.
        self.log.debug("Opening the socket")
        try:
            sock = bt.hci_open_dev(0)
            self.log.debug("Opened the socket")
        except Exception as exc:
            self.log.error("Error accessing bluetooth device: %s\n", exc, traceback.format_exec())
            return

        self.log.info("About to read inquiry mode")
        try:
            mode = self.read_inquiry_mode(sock)
        except Exception as exc:
            self.log.error("Error reading inquiry mode: %s", exc)
            sock.close()
            return

        self.log.debug("Inquiry mode is %s", mode)

        if mode != 1:
            self.log.debug("Writing inquire mode...")
            try:
                result = write_inquiry_mode(sock, 1)
            except Exception as exc:
                self.log.error("Error writing inquiry mode: %s", exc)
                return
            if result:
                self.log.error("Error while setting inquiry mode")
            self.log.debug("Result: %s", result)

        results = device_inquiry_with_rssi(sock)
        self.close()

        found = [rssi for rssi in results if rssi[0] == self.Address]

        self.log.info("Results = %s", results)

        # Return the first one.
        return found[0][1] if found else None

    def check_state(self):
        value = self.state
        rssi = self.get_rssi()

        # Update the near/far counts
        def update_count(amt, cnt):
            rval = cnt + amt
            if rval < 0:
                rval = 0
            elif rval > self.max_count:
                rval = self.max_count
            return rval

        if rssi is None:
            self.far_count = update_count(1, self.far_count)
            self.near_count = update_count(-1, self.near_count)
        elif rssi < -1:
            self.far_count = update_count(-1, self.far_count)
            self.near_count = update_count(1, self.near_count)

        if self.near_count > self.far_count and self.near_count > self.max_near:
            value = "ON"
        elif self.far_count > self.near_count and self.far_count > self.max_far:
            value = "OFF"

        if value != self.state:
            self.state = value
            self.publish_state()

    def publish_state(self):
       self._send(self.state, self.destination)
