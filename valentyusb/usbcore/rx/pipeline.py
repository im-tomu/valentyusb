#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

import unittest

from .bitstuff import RxBitstuffRemover
from .clock import RxClockDataRecovery
from .detect import RxPacketDetect
from .nrzi import RxNRZIDecoder
from .shifter import RxShifter
from ..utils.packet import b, nrzi


class RxPipeline(Module):
    def __init__(self):
        # 12MHz USB alignment pulse in 48MHz clock domain
        self.o_bit_strobe = Signal()

        # Reset state is J
        self.i_usbp = Signal(reset=1)
        self.i_usbn = Signal(reset=0)

        self.o_data_strobe = Signal()
        self.o_data_payload = Signal(8)

        self.o_pkt_end = Signal()

        # 48MHz domain
        # Clock recovery
        clock_data_recovery = RxClockDataRecovery(self.i_usbp, self.i_usbn)
        self.submodules.clock_data_recovery = ClockDomainsRenamer("usb_48")(clock_data_recovery)
        self.comb += [
            self.o_bit_strobe.eq(clock_data_recovery.line_state_valid),
        ]

        # NRZI decoding
        nrzi = RxNRZIDecoder()
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(nrzi)
        self.comb += [
            nrzi.i_valid.eq(self.o_bit_strobe),
            nrzi.i_dj.eq(clock_data_recovery.line_state_dj),
            nrzi.i_dk.eq(clock_data_recovery.line_state_dk),
            nrzi.i_se0.eq(clock_data_recovery.line_state_se0),
        ]

        # Cross the data from the 48MHz domain to the 12MHz domain
        bit_dat = Signal()
        bit_se0 = Signal()
        cdc_dat = cdc.MultiReg(nrzi.o_data, bit_dat, odomain="usb_12", n=3)
        cdc_se0 = cdc.MultiReg(nrzi.o_se0,  bit_se0, odomain="usb_12", n=3)
        self.specials += [cdc_dat, cdc_se0]

        # The packet detector resets the reset of the pipeline.
        reset = Signal()
        detect = RxPacketDetect()
        self.submodules.detect = detect = ClockDomainsRenamer("usb_12")(detect)
        self.comb += [
            detect.i_data.eq(bit_dat),
            reset.eq(~detect.o_pkt_active),
            detect.reset.eq(bit_se0),
        ]

        bitstuff = RxBitstuffRemover()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        self.comb += [
            bitstuff.reset.eq(reset),
            bitstuff.i_data.eq(bit_dat),
        ]

        # 1bit->8bit (1byte) serial to parallel conversion
        shifter = RxShifter(width=8)
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(shifter)
        self.comb += [
            shifter.reset.eq(reset),
            shifter.i_data.eq(bit_dat),
            shifter.ce.eq(~bitstuff.o_stall),
        ]
        self.comb += [
            self.o_data_strobe.eq(shifter.o_put),
            self.o_data_payload.eq(shifter.o_data[::-1]),
        ]

        # Packet ended signal
        self.sync.usb_12 += [
            self.o_pkt_end.eq(bit_se0),
        ]



class TestRxPipeline(unittest.TestCase):
    def test_pkt_decode(self):

        test_vectors = {
            "USB2 SOF token": dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100101 10000110 11000010 __111",
                data     = [b("10100101"), b("10000110"), b("11000010")],
                pkt_good = True,
            ),

            "USB2 SOF token - eop dribble 1": dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC  00
                value    = "11 00000001 10100101 10000110 11000010 1__111",
                data     = [b("10100101"), b("10000110"), b("11000010")],
                pkt_good = True,
            ),

            "USB2 SOF token - eop dribble 6": dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC       00
                value    = "11 00000001 10100101 10000110 11000010 111111__111",
                data     = [b("10100101"), b("10000110"), b("11000010")],
                pkt_good = True,
            ),

            "USB2 SOF token - bad pid": dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100100 10000110 11000010 __111",
                data     = [b("10100100"), b("10000110"), b("11000010")],
                pkt_good = False,
            ),

            "USB2 SOF token - bad crc5": dict(
                #              SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value    = "11 00000001 10100101 10000110 11000011 __111",
                data     = [b("10100101"), b("10000110"), b("11000011")],
                pkt_good = False,
            ),

            "USB2 ACK handshake": dict(
                #              SSSSSSSS PPPPPPPP 00
                value    = "11 00000001 01001011 __111",
                data     = [b("01001011")],
                pkt_good = True,
            ),

            "USB2 ACK handshake - pid error": dict(
                #              SSSSSSSS PPPPPPPP 00
                value    = "11 00000001 01001111 __111",
                data     = [b("01001111")],
                pkt_good = False,
            ),

            "USB2 ACK handshake - EOP dribble 1": dict(
                #              SSSSSSSS PPPPPPPP  00
                value    = "11 00000001 01001011 1__111",
                data     = [b("01001011")],
                pkt_good = True,
            ),

            "USB2 ACK handshake - EOP dribble 6": dict(
                #              SSSSSSSS PPPPPPPP       00
                value    = "11 00000001 01001011 111111__111",
                data     = [b("01001011")],
                pkt_good = True,
            ),

            "USB2 data with good CRC16": dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 __1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                pkt_good = True,
            ),

            "USB2 data with good CRC16 - 1 eop dribble": dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 1___1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                pkt_good = True,
            ),

            "USB2 data with good CRC16 - 6 eop dribble": dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 111111__1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                pkt_good = True,
            ),

            "USB2 data with bad CRC16": dict(
                value = "11 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101011 __1111",
                data  = [
                    b("11000011"),                                     # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0xd4,                                     # CRC16
                ],
                pkt_good = False,
            ),

            #dict(
            #    # USB2 SETUP and DATA
            #    value         = "11 00000001 10110100 00000000000 01000__111___1111111 00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 __1111",
            #    pid           = [0b1101,  0b0011],
            #    token_payload = [0,       1664],
            #    data_payload  = [[],      [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
            #    pkt_good      = [1,       1]
            #),
        }

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

            return data

        def stim(value, data, pkt_good):
            actual_data = yield from send(nrzi(value)+'J'*20)
            self.assertSequenceEqual(data, actual_data)

        for name, vector in test_vectors.items():
            with self.subTest(name=name):
                fname = name.replace(" ","_")
                dut = RxPipeline()
                run_simulation(
                    dut, stim(**vector),
                    vcd_name="vcd/test_decode_%s.vcd" % fname,
                    clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
                )


if __name__ == "__main__":
    import doctest
    doctest.testmod()
    unittest.main()
