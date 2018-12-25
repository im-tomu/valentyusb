#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

import unittest

from .bitstuff import TxBitstuffer
from .nrzi import TxNRZIEncoder
from .shifter import TxShifter
from utils.packet import b, nrzi, diff


class TxPipeline(Module):
    def __init__(self):
        self.i_bit_strobe = Signal()

        self.i_data_payload = Signal(8)
        self.o_data_strobe = Signal()

        self.i_oe = Signal()
        self.i_se0 = Signal()

        self.o_usbp = Signal()
        self.o_usbn = Signal()
        self.o_oe = Signal()

        bit_se0 = Signal()

        reset = Signal()
        stall = Signal()
        self.comb += [
            reset.eq(~self.i_oe),
        ]

        # 12MHz domain
        shifter = TxShifter(width=8)
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(shifter)
        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            self.o_data_strobe.eq(shifter.o_get),

            shifter.reset.eq(reset),
            shifter.ce.eq(~stall),
        ]

        bitstuff = TxBitstuffer()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        self.comb += [
            bitstuff.i_data.eq(shifter.o_data),
            bitstuff.reset.eq(reset),
            stall.eq(bitstuff.o_stall),
        ]

        # Cross the data from the 12MHz domain to the 48MHz domain
        fit_dat = Signal()
        fit_se0 = Signal()
        cdc_dat = cdc.MultiReg(bitstuff.o_data, fit_dat, odomain="usb_48", n=3)
        cdc_se0 = cdc.MultiReg(self.i_se0, fit_se0, odomain="usb_48", n=3)
        self.specials += [cdc_dat, cdc_se0]

        # 48MHz domain
        # NRZI decoding
        nrzi = TxNRZIEncoder()
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(nrzi)
        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(fit_dat),
            nrzi.i_oe.eq(self.i_oe),
            nrzi.i_se0.eq(fit_se0),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),
        ]




class TestTxPipeline(unittest.TestCase):
    def test_pkt_decode(self):

        test_vectors = {
            "USB2 SOF token": dict(
                data  = [b("00000001"), b("10100101"), b("10000110"), b("11000010")],
                oe    = "00 11111111 11111111 11111111 11111111 000000",
                se0   = "00 00000000 00000000 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value =    "00000001 10100101 10000110 11000010 __",
            ),

            "USB2 SOF token - eop dribble 1": dict(
                data  = [b("00000001"), b("10100101"), b("10000110"), b("11000010")],
                oe    = "00 11111111 11111111 11111111 11111111 1000000",
                se0   = "00 00000000 00000000 00000000 00000000 0110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC  00
                value =    "00000001 10100101 10000110 11000010 1__",
            ),

            "USB2 SOF token - eop dribble 6": dict(
                data  = [b("00000001"), b("10100101"), b("10000110"), b("11000010")],
                oe    = "00 11111111 11111111 11111111 11111111 111111000000",
                se0   = "00 00000000 00000000 00000000 00000000 000000110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC       00
                value =    "00000001 10100101 10000110 11000010 111111__",
            ),

            "USB2 SOF token - bad pid": dict(
                data  = [b("00000001"), b("10100100"), b("10000110"), b("11000010")],
                oe    = "00 11111111 11111111 11111111 11111111 000000",
                se0   = "00 00000000 00000000 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value =    "00000001 10100100 10000110 11000010 __",
            ),

            "USB2 SOF token - bad crc5": dict(
                data  = [b("00000001"), b("10100101"), b("10000110"), b("11000011")],
                oe    = "00 11111111 11111111 11111111 11111111 000000",
                se0   = "00 00000000 00000000 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value =    "00000001 10100101 10000110 11000011 __",
            ),

            "USB2 ACK handshake": dict(
                data  = [b("00000001"), b("01001011")],
                oe    = "00 11111111 11111111 000000",
                se0   = "00 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP 00
                value =    "00000001 01001011 __",
            ),

            "USB2 ACK handshake - pid error": dict(
                data  = [b("00000001"), b("01001111")],
                oe    = "00 11111111 11111111 000000",
                se0   = "00 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP 00
                value =    "00000001 01001111 __",
            ),

            "USB2 ACK handshake - EOP dribble 1": dict(
                data  = [b("00000001"), b("01001011")],
                oe    = "00 11111111 11111111 1000000",
                se0   = "00 00000000 00000000 0110000",
                #           SSSSSSSS PPPPPPPP  00
                value =    "00000001 01001011 1__",
            ),

            "USB2 ACK handshake - EOP dribble 6": dict(
                data  = [b("00000001"), b("01001011")],
                oe    = "00 11111111 11111111 111111000000",
                se0   = "00 00000000 00000000 000000110000",
                #           SSSSSSSS PPPPPPPP       00
                value =    "00000001 01001011 111111__",
            ),

            "USB2 data with good CRC16": dict(
                data  = [
                    b("00000001"),                                  # Sync
                    b("11000011"),                                  # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 110000",
                se0   = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 110000",
                value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 __",
            ),

            "USB2 data with good CRC16 - 1 eop dribble": dict(
                data  = [
                    b("00000001"),                                  # Sync
                    b("11000011"),                                  # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 1110000",
                se0   = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 0110000",
                value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 1__",
            ),

            "USB2 data with good CRC16 - 6 eop dribble": dict(
                data  = [
                    b("00000001"),                                  # Sync
                    b("11000011"),                                  # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 111111000000",
                se0   = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 000000110000",
                value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 111111__",
            ),

            "USB2 data with bad CRC16": dict(
                data  = [
                    b("00000001"),                                  # Sync
                    b("11000011"),                                  # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0xd4,                                     # CRC16
                ],
                oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 000000",
                se0   = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 110000",
                value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101011 __",
            ),
        }

        def send(data, oe, se0):
            oe = oe.replace(' ', '')
            se0 = se0.replace(' ', '')

            clk12 = ClockSignal("usb_12")
            clk48 = ClockSignal("usb_48")

            def clk12_edge():
                data_strobe = yield dut.o_data_strobe
                if data_strobe:
                    if data:
                        yield dut.i_data_payload.eq(data.pop(0))
                    else:
                        yield dut.i_data_payload.eq(0xff)

            usb = {
                'p': "",
                'n': "",
            }
            def clk48_edge(clk48=[0]):
                j = clk48[0]

                u = int(j/4)
                if u < len(oe):
                    yield dut.i_oe.eq(int(oe[u]))
                    yield dut.i_se0.eq(int(se0[u]))

                if j % 4 == 0:
                    yield dut.i_bit_strobe.eq(1)
                else:
                    yield dut.i_bit_strobe.eq(0)

                usb['p'] += str((yield dut.o_usbp))
                usb['n'] += str((yield dut.o_usbn))

                clk48[0] += 1

            def tick(last={'clk12': None, 'clk48':None}):
                current_clk12 = yield clk12
                if current_clk12 and not last['clk12']:
                    yield from clk12_edge()
                last['clk12'] = current_clk12

                current_clk48 = yield clk48
                if current_clk48 and not last['clk48']:
                    yield from clk48_edge()
                last['clk48'] = current_clk48

                yield

            yield dut.i_data_payload.eq(0)
            i = 0
            while usb['p'][-(4*4):] != '1'*(4*4) and i < 1000:
                yield from tick()
                i += 1

            #assert usbn[20:] == 'J'*20
            return usb['p'][2:], usb['n'][2:]

        def stim(value, data, oe, se0):
            print()
            print(value)
            print(nrzi(value)+'J'*20)
            print()
            expected_usbp, expected_usbn = diff('J'*(2*4)+nrzi(value)+'J'*(4*4))
            actual_usbp, actual_usbn = yield from send(data, oe, se0)
            print()
            print("usbp")
            print(expected_usbp)
            print(actual_usbp)
            print()
            print("usbn")
            print(expected_usbn)
            print(actual_usbn)
            print()
            self.assertSequenceEqual(expected_usbp, actual_usbp)
            self.assertSequenceEqual(expected_usbn, actual_usbn)

        for name, vector in list(test_vectors.items())[:1]:
            with self.subTest(name=name):
                fname = name.replace(" ","_")
                dut = TxPipeline()
                run_simulation(dut, stim(**vector), vcd_name="vcd/test_pipeline_%s.vcd" % fname, clocks={"sys": 10, "usb_48": 40, "usb_12": 160})


if __name__ == "__main__":
    unittest.main()
