#!/usr/bin/env python3

import unittest

from migen import *
from migen.genlib import cdc

from .bitstuff import RxBitstuffRemover
from .clock import RxClockDataRecovery
from .detect import RxPacketDetect
from .nrzi import RxNRZIDecoder
from .shifter import RxShifter
from ..utils.packet import b, nrzi
from ..test.common import BaseUsbTestCase

from .pipeline import RxPipeline


class TestRxPipeline(BaseUsbTestCase):
    def pkt_decode_test(self, vector, name):
        def send(value):
            clk12 = ClockSignal("usb_12")

            def clk12_edge():
                data_strobe  = yield dut.o_data_strobe
                data_payload = yield dut.o_data_payload
                if data_strobe:
                    data.append(data_payload)

            def tick(last_clk12=[None]):
                current_clk12 = yield clk12
                if current_clk12 and not last_clk12[0]:
                    yield from clk12_edge()
                last_clk12[0] = current_clk12
                yield

            for i in range(0, 100):
                yield

            data = []
            for i in range(len(value)):
                v = value[i]
                if v == ' ':
                    continue
                elif v == '_':
                    # SE0 - both lines pulled low
                    yield dut.i_usbp.eq(0)
                    yield dut.i_usbn.eq(0)
                elif v == 'J':
                    yield dut.i_usbp.eq(1)
                    yield dut.i_usbn.eq(0)
                elif v == 'K':
                    yield dut.i_usbp.eq(0)
                    yield dut.i_usbn.eq(1)
                else:
                    assert False, "Unknown value: %s" % v

                for i in range(0, 4):
                    yield from tick()
            for i in range(0, 300):
                yield
            return data

        def stim(value, data, pkt_good):
            actual_data = yield from send(nrzi(value)+'J'*20)
            msg = "\n"

            loop=0
            msg = msg + "Wanted: ["
            for var in data:
                if loop > 0:
                    msg = msg + ", "
                msg = msg + "0x{:02x}".format(var)
                loop = loop + 1
            msg = msg + "]\n"

            loop=0
            msg = msg + "   Got: ["
            for var in actual_data:
                if loop > 0:
                    msg = msg + ", "
                msg = msg + "0x{:02x}".format(var)
                loop = loop + 1
            msg = msg + "]"
            self.assertSequenceEqual(data, actual_data, msg=msg)

        with self.subTest(name=name):
            fname = name.replace(" ","_")
            dut = RxPipeline()
            run_simulation(
                dut, stim(**vector),
                vcd_name=self.make_vcd_name(testsuffix=fname),
                clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
            )

    def test_usb2_sof_stuffed_mid(self):
        return self.pkt_decode_test(
            dict(
                value    = "11 00000001 10100101 11111011 100111100 __111",
                data     = [b("10100101"), b("11111111"), b("00111100")],
                pkt_good = True,
            ), "USB2 SOF Stuffed Middle")

    def test_usb2_sof_stuffed_end(self):
        return self.pkt_decode_test(
            dict(
                value    = "11 00000001 10100101 11100001 01111110 __111",
                data     = [b("10100101"), b("11100001"), b("01111110")],
                pkt_good = True,
            ), "USB2 SOF Stuffed End")

    def test_usb2_sof_token(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100101 00010010 11000101 __111",
                data     = [b("10100101"), b("00010010"), b("11000101")],
                pkt_good = True,
            ), "USB2 SOF token")

    def test_usb2_sof_token_1(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100101 11011100 10100011 __111",
                data     = [b("10100101"), b("11011100"), b("10100011")],
                pkt_good = True,
            ), "USB2 SOF token 1")

    def test_usb2_sof_token_eop_dribble_1(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC  00
                value    = "11 00000001 10100101 10000110 11000010 1__111",
                data     = [b("10100101"), b("10000110"), b("11000010")],
                pkt_good = True,
            ), "USB2 SOF token - eop dribble 1")

    def test_usb2_sof_token_eop_dribble_6(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC       00
                value    = "11 00000001 10100101 10000110 11000010 111111__111",
                data     = [b("10100101"), b("10000110"), b("11000010")],
                pkt_good = True,
            ), "USB2 SOF token - eop dribble 6")

    def test_usb2_sof_token_bad_pid(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100100 10000110 11000010 __111",
                data     = [b("10100100"), b("10000110"), b("11000010")],
                pkt_good = False,
            ), "USB2 SOF token - bad pid")

    def test_usb2_sof_token_bad_crc5(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100101 10000110 11000011 __111",
                data     = [b("10100101"), b("10000110"), b("11000011")],
                pkt_good = False,
            ), "USB2 SOF token - bad crc5")

    def test_usb2_ack_handshake(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP 00
                value    = "11 00000001 01001011 __111",
                data     = [b("01001011")],
                pkt_good = True,
            ), "USB2 ACK handshake")

    def test_usb2_ack_handshake_pid_error(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP 00
                value    = "11 00000001 01001111 __111",
                data     = [b("01001111")],
                pkt_good = False,
            ), "USB2 ACK handshake - pid error")

    def test_usb2_ack_handshake_eop_dribble_1(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP  00
                value    = "11 00000001 01001011 1__111",
                data     = [b("01001011")],
                pkt_good = True,
            ), "USB2 ACK handshake - EOP dribble 1")

    def test_usb2_ack_handshake_eop_dribble_6(self):
        return self.pkt_decode_test(
            dict(
                #              SSSSSSSS PPPPPPPP       00
                value    = "11 00000001 01001011 111111__111",
                data     = [b("01001011")],
                pkt_good = True,
            ), "USB2 ACK handshake - EOP dribble 6")

    def test_usb2_data_with_good_crc16(self):
        return self.pkt_decode_test(
            dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 __1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                pkt_good = True,
            ), "USB2 data with good CRC16")

    def test_usb2_data_with_good_crc16_1_eop_dribble(self):
        return self.pkt_decode_test(
            dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 1___1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                pkt_good = True,
            ), "USB2 data with good CRC16 - 1 eop dribble")

    def test_usb2_data_with_good_crc16_6_eop_dribble(self):
        return self.pkt_decode_test(
            dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 111111__1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                pkt_good = True,
            ), "USB2 data with good CRC16 - 6 eop dribble")

    def test_usb2_data_with_bad_crc16(self):
        return self.pkt_decode_test(
            dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101011 __1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0xd4,                                     # CRC16
                ],
                pkt_good = False,
            ), "USB2 data with bad CRC16")

            #dict(
            #    # USB2 SETUP and DATA
            #    value         = "11 00000001 10110100 00000000000 01000__111___1111111 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 __1111",
            #    pid           = [0b1101,  0b0011],
            #    token_payload = [0,       1664],
            #    data_payload  = [[],      [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
            #    pkt_good      = [1,       1]
            #),

if __name__ == "__main__":
    import doctest
    doctest.testmod()
    unittest.main()
