#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

import unittest

from .bitstuff import TxBitstuffer
from .nrzi import TxNRZIEncoder
from .shifter import TxShifter
from ..utils.packet import b, nrzi, diff
from ..test.common import BaseUsbTestCase


class TxPipeline(Module):
    def __init__(self):
        self.i_bit_strobe = Signal()

        self.i_data_payload = Signal(8)
        self.o_data_strobe = Signal()

        self.i_oe = Signal()

        self.o_usbp = Signal()
        self.o_usbn = Signal()
        self.o_oe = Signal()

        reset = Signal()
        stall = Signal()

        # 12MHz domain
        shifter = TxShifter(width=8)
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(shifter)
        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            self.o_data_strobe.eq(shifter.o_get & ~stall & self.i_oe),

            shifter.reset.eq(reset),
            shifter.ce.eq(~stall),
        ]

        # FIXME: This is a horrible hack
        stalled_reset = Signal()
        reset_n1 = Signal() # Need to reset the bit stuffer 1 cycle after the shifter.
        i_oe_n1 = Signal()  # 1 cycle delay inside bit stuffer
        i_oe_n2 = Signal()  # Where does this delay come from?
        self.sync.usb_12 += [
            If(shifter.o_empty,
                stalled_reset.eq(~self.i_oe),
            ),
            If(~stall,
                reset.eq(stalled_reset),
            ),
            If(~stall,
                If(shifter.o_get,
                    i_oe_n1.eq(self.i_oe),
                ),
                reset_n1.eq(reset),
                i_oe_n2.eq(i_oe_n1),
            ),
        ]

        self.comb += [
        ]

        bitstuff = TxBitstuffer()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        self.comb += [
            bitstuff.i_data.eq(shifter.o_data),
            bitstuff.reset.eq(reset_n1),
            stall.eq(bitstuff.o_stall),
        ]

        # Cross the data from the 12MHz domain to the 48MHz domain
        fit_dat = Signal()
        fit_oe  = Signal()
        cdc_dat = cdc.MultiReg(bitstuff.o_data, fit_dat, odomain="usb_48", n=3)
        cdc_oe  = cdc.MultiReg(i_oe_n2, fit_oe, odomain="usb_48", n=3)
        self.specials += [cdc_dat, cdc_oe]

        # 48MHz domain
        # NRZI decoding
        nrzi = TxNRZIEncoder()
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(nrzi)
        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(fit_dat),
            nrzi.i_oe.eq(fit_oe),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),
        ]




class TestTxPipeline(BaseUsbTestCase):
    maxDiff=None

    def test_pkt_decode(self):

        test_vectors = {
            # Passed
            "USB2 SOF token": dict(
                data  = [b("10000001"), b("10100101"), b("10000110"), b("11000010")],
                oe    = "00 11111111 11111111 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value =    "00000001 10100101 10000110 11000010 __",
            ),

            #"USB2 SOF token - eop dribble 1": dict(
            #    data  = [b("00000001"), b("10100101"), b("10000110"), b("11000010")],
            #    oe    = "00 11111111 11111111 11111111 11111111 1000000",
            #    #se0  = "00 00000000 00000000 00000000 00000000 0110000",
            #    #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC  00
            #    value =    "00000001 10100101 10000110 11000010 1__",
            #),

            #"USB2 SOF token - eop dribble 6": dict(
            #    data  = [b("00000001"), b("10100101"), b("10000110"), b("11000010")],
            #    oe    = "00 11111111 11111111 11111111 11111111 111111000000",
            #    #se0  = "00 00000000 00000000 00000000 00000000 000000110000",
            #    #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC       00
            #    value =    "00000001 10100101 10000110 11000010 111111__",
            #),

            # Passed
            "USB2 SOF token - bad pid": dict(
                data  = [b("00000001"), b("10100100"), b("10000110"), b("11000010")],
                oe    = "00 11111111 11111111 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value =    "00000001 10100100 10000110 11000010 __",
            ),

            "USB2 SOF token - bad crc5": dict(
                data  = [b("00000001"), b("10100101"), b("10000110"), b("11000011")],
                oe    = "00 11111111 11111111 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP AAAAAAAE EEECCCCC 00
                value =    "00000001 10100101 10000110 11000011 __",
            ),

            "USB2 ACK handshake": dict(
                data  = [b("00000001"), b("01001011")],
                oe    = "00 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP 00
                value =    "00000001 01001011 __",
            ),

            "USB2 ACK handshake - pid error": dict(
                data  = [b("00000001"), b("01001111")],
                oe    = "00 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 110000",
                #           SSSSSSSS PPPPPPPP 00
                value =    "00000001 01001111 __",
            ),

            #"USB2 ACK handshake - EOP dribble 1": dict(
            #    data  = [b("00000001"), b("01001011")],
            #    oe    = "00 11111111 11111111 1000000",
            #    #se0  = "00 00000000 00000000 0110000",
            #    #           SSSSSSSS PPPPPPPP  00
            #    value =    "00000001 01001011 1__",
            #),

            #"USB2 ACK handshake - EOP dribble 6": dict(
            #    data  = [b("00000001"), b("01001011")],
            #    oe    = "00 11111111 11111111 111111000000",
            #    #se0  = "00 00000000 00000000 000000110000",
            #    #           SSSSSSSS PPPPPPPP       00
            #    value =    "00000001 01001011 111111__",
            #),

            "USB2 data with good CRC16": dict(
                data  = [
                    b("00000001"),                                  # Sync
                    b("11000011"),                                  # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0x94,                                     # CRC16
                ],
                oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 110000",
                value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 __",
            ),

            #"USB2 data with good CRC16 - 1 eop dribble": dict(
            #    data  = [
            #        b("00000001"),                                  # Sync
            #        b("11000011"),                                  # PID
            #        0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
            #        0xdd, 0x94,                                     # CRC16
            #    ],
            #    oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 1110000",
            #    #se0  = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 0110000",
            #    value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 1__",
            #),

            #"USB2 data with good CRC16 - 6 eop dribble": dict(
            #    data  = [
            #        b("00000001"),                                  # Sync
            #        b("11000011"),                                  # PID
            #        0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
            #        0xdd, 0x94,                                     # CRC16
            #    ],
            #    oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 111111000000",
            #    #se0  = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 000000110000",
            #    value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101001 111111__",
            #),

            "USB2 data with bad CRC16": dict(
                data  = [
                    b("00000001"),                                  # Sync
                    b("11000011"),                                  # PID
                    0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, # Data payload
                    0xdd, 0xd4,                                     # CRC16
                ],
                oe    = "00 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111 000000",
                #se0  = "00 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 110000",
                value =    "00000001 11000011 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 10111011 00101011 __",
            ),
        }

        PAD=8
        def send(data, oe):
            oe = oe.replace(' ', '')

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
            N = 4*8
            while usb['p'][PAD:][-N:] != '1'*(N) and i < 10000:
                yield from tick()
                i += 1

            #assert usbn[20:] == 'J'*20
            return usb['p'][2:], usb['n'][2:]

        def stim(value, data, oe):
            expected_usbp, expected_usbn = diff('J'*(2*4)+nrzi(value)+'J'*(4*4))
            assert len(expected_usbp) == len(expected_usbn)
            actual_usbp, actual_usbn = yield from send(data, oe)
            self.assertSequenceEqual(expected_usbp, actual_usbp[PAD:len(expected_usbp)+PAD])
            self.assertSequenceEqual(expected_usbn, actual_usbn[PAD:len(expected_usbn)+PAD])

        for name, vector in list(test_vectors.items()):
            with self.subTest(name=name):
                fname = name.replace(" ","_")
                dut = TxPipeline()
                run_simulation(dut, stim(**vector),
                    vcd_name=self.make_vcd_name(
                        basename="usbcore.tx.pipeline." + fname),
                    clocks={"sys": 10, "usb_48": 40, "usb_12": 160})


if __name__ == "__main__":
    unittest.main()
