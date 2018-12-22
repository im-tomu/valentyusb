#!/usr/bin/env python3

from itertools import zip_longest

import CrcMoose3 as crc

import unittest
from unittest import TestCase
from usbcore import *

class TestRxClockDataRecovery(TestCase):
    def test_basic_recovery(self):
        """
        This test covers basic clock and data recovery.
        """

        def get_output():
            """
            Record data output when line_state_valid is asserted.
            """
            valid = yield dut.line_state_valid
            if valid == 1:
                dj = yield dut.line_state_dj
                dk = yield dut.line_state_dk
                se0 = yield dut.line_state_se0
                se1 = yield dut.line_state_se1

                out = "%d%d%d%d" % (dj, dk, se0, se1)

                return {
                    "1000" : "j",
                    "0100" : "k",
                    "0010" : "0",
                    "0001" : "1",
                }[out]

            else:
                return ""

        def stim(glitch=-1):
            out_seq = ""
            clock = 0
            for bit in seq + "0":
                for i in range(4):
                    if clock != glitch:
                        yield usbp_raw.eq({'j':1,'k':0,'0':0,'1':1}[bit])
                    yield usbn_raw.eq({'j':0,'k':1,'0':0,'1':1}[bit])
                    yield
                    clock += 1
                    out_seq += yield from get_output()
            self.assertEqual(out_seq, "0" + seq)

        test_sequences = [
            "j",
            "k",
            "0",
            "1",
            "jk01",
            "jjjkj0j1kjkkk0k10j0k00011j1k1011"
        ]

        for seq in test_sequences:
            with self.subTest(seq=seq):
                usbp_raw = Signal()
                usbn_raw = Signal()

                dut = RxClockDataRecovery(usbp_raw, usbn_raw)

                run_simulation(dut, stim(), vcd_name="vcd/test_basic_recovery_%s.vcd" % seq)


        long_test_sequences = [
            "jjjkj0j1kjkkk0k10j0k00011j1k1011",
            "kkkkk0k0kjjjk0kkkkjjjkjkjkjjj0kj"
        ]

        for seq in long_test_sequences:
            for glitch in range(0, 32, 8):
                with self.subTest(seq=seq, glitch=glitch):
                    usbp_raw = Signal()
                    usbn_raw = Signal()

                    dut = RxClockDataRecovery(usbp_raw, usbn_raw)

                    run_simulation(dut, stim(glitch), vcd_name="vcd/test_basic_recovery_%s_%d.vcd" % (seq, glitch))



class TestRxNRZIDecoder(TestCase):
    def test_nrzi(self):

        def send(valid, value):
            valid += "_"
            value += "_"
            output = ""
            for i in range(len(valid)):
                yield i_valid.eq(valid[i] == '-')
                yield i_dj.eq(value[i] == 'j')
                yield i_dk.eq(value[i] == 'k')
                yield i_se0.eq(value[i] == '_')
                yield

                o_valid = yield dut.o_valid
                if o_valid:
                    data = yield dut.o_data
                    se0 = yield dut.o_se0

                    out = "%d%d" % (data, se0)

                    output += {
                        "10" : "1",
                        "00" : "0",
                        "01" : "_",
                        "11" : "_"
                    }[out]
            return output

        test_vectors = [
            dict(
                # USB2 Spec, 7.1.8
                valid  = "-----------------",
                value  = "jkkkjjkkjkjjkjjjk",
                output = "10110101000100110"
            ),

            dict(
                # USB2 Spec, 7.1.9.1
                valid  = "--------------------",
                value  = "jkjkjkjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls)
                valid  = "------___--------------",
                value  = "jkjkjkkkkjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls 2)
                valid  = "-------___-------------",
                value  = "jkjkjkjjjjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls 3)
                valid  = "-------___-------------",
                value  = "jkjkjkjkkkkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls, se0 glitch)
                valid  = "-------___-------------",
                value  = "jkjkjkj__kkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # Captured setup packet
                valid  = "------------------------------------",
                value  = "jkjkjkjkkkjjjkkjkjkjkjkjkjkjkkjkj__j",
                output = "100000001101101000000000000001000__1"
            ),

            dict(
                # Captured setup packet (pipeline stalls)
                valid  = "-___----___--------___-___-___-___----------------___-___---",
                value  = "jjjjkjkjjkkkjkkkjjjjjkkkkkkkkkjjjjkjkjkjkjkjkjkkjkkkkj_____j",
                output = "100000001101101000000000000001000__1"
            )

        ]

        def stim(valid, value, output):
            actual_output = yield from send(valid, value)
            self.assertEqual(actual_output, output)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                i_valid = Signal()
                i_dj = Signal()
                i_dk = Signal()
                i_se0 = Signal()

                dut = RxNRZIDecoder(i_valid, i_dj, i_dk, i_se0)

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_nrzi_%d.vcd" % i)
                i += 1




class TestRxBitstuffRemover(TestCase):
    def test_bitstuff(self):

        def send(valid, value):
            valid += "_"
            value += "_"
            output = ""
            for i in range(len(valid)):
                yield i_valid.eq(valid[i] == '-')
                yield i_data.eq(value[i] == '1')
                yield i_se0.eq(value[i] == '_')
                yield

                o_valid = yield dut.o_valid
                bitstuff_error = yield dut.o_bitstuff_error
                if o_valid or bitstuff_error:
                    data = yield dut.o_data
                    se0 = yield dut.o_se0

                    out = "%d%d%d" % (data, se0, bitstuff_error)

                    output += {
                        "100" : "1",
                        "101" : "e",
                        "000" : "0",
                        "010" : "_",
                        "110" : "_"
                    }[out]
            return output

        test_vectors = [
            dict(
                # Basic bitstuff scenario
                valid  = "-------",
                value  = "1111110",
                output = "111111"
            ),

            dict(
                # Basic bitstuff scenario (valid gap)
                valid  = "---___----",
                value  = "111___1110",
                output = "111111"
            ),

            dict(
                # Basic bitstuff scenario (valid gap)
                valid  = "---___----",
                value  = "1111111110",
                output = "111111"
            ),

            dict(
                # Basic bitstuff scenario (valid gap)
                valid  = "---___----",
                value  = "1110001110",
                output = "111111"
            ),

            dict(
                # Basic bitstuff scenario (valid gap)
                valid  = "---___-____---",
                value  = "11100010000110",
                output = "111111"
            ),


            dict(
                # Basic bitstuff error
                valid  = "-------",
                value  = "1111111",
                output = "111111e"
            ),

            dict(
                # Multiple bitstuff scenario
                valid  = "---------------------",
                value  = "111111011111101111110",
                output = "111111111111111111"
            ),

            dict(
                # Mixed bitstuff error
                valid  = "---------------------------------",
                value  = "111111111111101111110111111111111",
                output = "111111e111111111111111111e11111"
            ),

            dict(
                # Idle, Packet, Idle
                valid  = "-------------------------------",
                value  = "111110000000111111011101__11111",
                output = "11111000000011111111101__11111"
            ),

            dict(
                # Idle, Packet, Idle, Packet, Idle
                valid  = "--------------------------------------------------------------",
                value  = "111110000000111111011101__11111111110000000111111011101__11111",
                output = "11111000000011111111101__111111e111000000011111111101__11111"
            ),

            dict(
                # Captured setup packet (no bitstuff)
                valid  = "------------------------------------",
                value  = "100000001101101000000000000001000__1",
                output = "100000001101101000000000000001000__1"
            )
        ]

        def stim(valid, value, output):
            actual_output = yield from send(valid, value)
            self.assertEqual(actual_output, output)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                i_valid = Signal()
                i_data = Signal()
                i_se0 = Signal()

                dut = RxBitstuffRemover(i_valid, i_data, i_se0)

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_bitstuff_%d.vcd" % i)
                i += 1




class TestRxPacketDetect(TestCase):
    def test_packet_detect(self):

        test_vectors = [
            dict(
                # SE0, Idle
                valid    = "------------------------------",
                value    = "______________1111111111111111",
                output_1 = "                               ",
                output_2 = "_______________________________"
            ),

            dict(
                # Idle, Packet, Idle
                valid    = "------------------------------",
                value    = "11111000000011111111101__11111",
                output_1 = "             S          E      ",
                output_2 = "_____________-----------_______"
            ),

            dict(
                # Idle, Packet, Idle (pipeline stall)
                valid    = "-------------___-----------------",
                value    = "11111000000011111111111101__11111",
                output_1 = "             S             E      ",
                output_2 = "_____________--------------_______"
            ),

            dict(
                # Idle, Packet, Idle (pipeline stalls)
                valid    = "-----___---___-----___-----------------",
                value    = "11111111000___000011111111111101__11111",
                output_1 = "                   S             E      ",
                output_2 = "___________________--------------_______"
            ),

            dict(
                # Idle, Packet, Idle, Packet, Idle
                valid    = "------------------------------------------------------------",
                value    = "11111000000011111111101__1111111111000000011111111101__11111",
                output_1 = "             S          E                  S          E      ",
                output_2 = "_____________-----------___________________-----------_______"
            ),

            dict(
                # Idle, Short Sync Packet, Idle
                valid    = "----------------------------",
                value    = "111110000011111111101__11111",
                output_1 = "           S          E      ",
                output_2 = "___________-----------_______"
            ),

            dict(
                # Idle Glitch
                valid    = "------------------------------",
                value    = "11111111110011111111_1111__111",
                output_1 = "                               ",
                output_2 = "_______________________________"
            ),
        ]

        def send(valid, value):
            valid += "_"
            value += "_"
            output_1 = ""
            output_2 = ""
            for i in range(len(valid)):
                yield i_valid.eq(valid[i] == '-')
                yield i_data.eq(value[i] == '1')
                yield i_se0.eq(value[i] == '_')
                yield

                pkt_start = yield dut.o_pkt_start
                pkt_end = yield dut.o_pkt_end

                out = "%d%d" % (pkt_start, pkt_end)

                output_1 += {
                    "10" : "S",
                    "01" : "E",
                    "00" : " ",
                }[out]

                pkt_active = yield dut.o_pkt_active

                out = "%d" % (pkt_active)

                output_2 += {
                    "1" : "-",
                    "0" : "_",
                }[out]

            return output_1, output_2

        def stim(valid, value, output_1, output_2):
            actual_output_1, actual_output_2 = yield from send(valid, value)
            self.assertEqual(actual_output_1, output_1)
            self.assertEqual(actual_output_2, output_2)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                i_valid = Signal()
                i_data = Signal()
                i_se0 = Signal()

                dut = RxPacketDetect(i_valid, i_data, i_se0)

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_packet_det_%d.vcd" % i)
                i += 1





class TestRxShifter(TestCase):
    def test_shifter(self):
        test_vectors = [
            # 0
            dict(
                # basic shift in
                reset    = "-|________|",
                valid    = "_|01234567|",
                data     = "1|00000000|",
                put      = "_|_______-|",
                output   = [0b00000000]
            ),
            # 1
            dict(
                # basic shift in
                reset    = "-|________|",
                valid    = "_|01234567|",
                data     = "1|11111111|",
                put      = "_|_______-|",
                output   = [0b11111111]
            ),
            # 2
            dict(
                # basic shift in
                reset    = "-|________|",
                valid    = "_|01234567|",
                data     = "1|10000000|",
                put      = "_|_______-|",
                output   = [0b00000001]
            ),
            # 3
            dict(
                # basic shift in
                reset    = "-|________|",
                valid    = "_|01234567|",
                data     = "1|00000001|",
                put      = "_|_______-|",
                output   = [0b10000000]
            ),
            # 4
            dict(
                # basic shift in
                reset    = "-|________|",
                valid    = "_|01234567|",
                data     = "1|01111110|",
                put      = "_|_______-|",
                output   = [0b01111110]
                #output   = [127],
            ),
            # 5
            dict(
                # basic shift in
                reset    = "-|________|",
                valid    = "_|01234567|",
                data     = "0|01110100|",
                put      = "_|_______-|",
                output   = [0b00101110]
                #output   = [0x2E],
            ),
            # 6
            dict(
                # basic shift in, 2 bytes
                reset    = "-|________|||________|",
                valid    = "_|01234567|||01234567|",
                data     = "0|01110100|||10101000|",
                put      = "_|_______-|||_______-|",
                output   = [0b00101110,0b00010101]
                #output   = [46, 21]
            ),
            # 7
            dict(
                # basic shift in (short pipeline stall)
                reset    = "-|_________|",
                valid    = "_|0123_4567|",
                data     = "0|011100100|",
                put      = "_|________-|",
                output   = [46]
            ),
            # 8
            dict(
                # basic shift in (long pipeline stall)
                reset    = "-|___________|",
                valid    = "_|0123___4567|",
                data     = "0|01110000100|",
                put      = "_|__________-|",
                output   = [46]
            ),
            # 9
            dict(
                # basic shift in (multiple long pipeline stall)
                reset    = "-|____________________|",
                valid    = "_|0___123___4___56___7|",
                data     = "0|00001110000111101110|",
                put      = "_|___________________-|",
                output   = [46]
            ),
            # 10
            dict(
                # multiple resets
                reset    = "-|________|______-|___-|________|",
                valid    = "_|01234567|0123456|0123|01234567|",
                data     = "0|01110100|0011010|0111|10101000|",
                put      = "_|_______-|_______|____|_______-|",
                output   = [46, 21]
            ),
            # 11
            dict(
                # multiple resets (tight timing)
                reset    = "-|________|-|________|",
                valid    = "_|01234567|0|01234567|",
                data     = "0|01110100|1|00101000|",
                put      = "_|_______-|_|_______-|",
                output   = [46, 20]
            ),
        ]

        actual_output = []
        def send(reset, valid, data , put=None, output=None):
            def clock():
                yield
                o_put = yield dut.o_put
                if o_put:
                    last_output = yield dut.o_output
                    actual_output.append(last_output)

            for i in range(len(valid)):
                if valid[i] == '|':
                    assert reset[i] == '|', reset[i]
                    assert data [i] == '|', data [i]
                    continue
                yield dut.i_reset.eq(reset[i] == '-')
                yield dut.i_data.eq(data [i] == '1')
                yield from clock()
                yield from clock()
                yield dut.i_valid.eq(valid[i] != '_')
                yield from clock()
                yield dut.i_valid.eq(0)
                yield from clock()

        for i, vector in enumerate(test_vectors):
            with self.subTest(i=i, vector=vector):
                dut = RxShifter(8)

                actual_output.clear()
                run_simulation(dut, send(**vector), vcd_name="vcd/test_shifter_%d.vcd" % i)
                self.assertEqual(actual_output, vector['output'])





class TestRxCrcChecker(TestCase):
    def test_shifter(self):
        def send(reset, valid, value):
            crc_good = ""
            for i in range(len(valid)):
                yield i_reset.eq(reset[i] == '-')
                yield i_valid.eq(valid[i] == '-')
                yield i_data.eq(value[i] == '1')
                yield

                o_crc_good = yield dut.o_crc_good

                out = "%d" % (o_crc_good)

                crc_good += {
                    "1" : "-",
                    "0" : "_",
                }[out]

            return crc_good

        test_vectors = [
            dict(
                # USB2 token with good CRC5 (1)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00000000000001000000",
                crc_good    = "_______-__________--"
            ),

            dict(
                # USB2 token with good CRC5 and pipeline stalls (1)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-_______________________________",
                valid       = "_-___-___------------___-___-___",
                value       = "00000011100000000001011100000000",
                crc_good    = "_____________-________________--"
            ),

            dict(
            # USB2 token with bad CRC5 (1)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00010000000001000000",
                crc_good    = "______-________-____"
            ),

            dict(
                # USB2 token with good CRC5 (2)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00000011011011101000",
                crc_good    = "_______-__________--"
            ),

            dict(
                # USB2 token with bad CRC5 (2)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00010011011011101000",
                crc_good    = "______-_____________"
            ),

            dict(
                # Two USB2 token with good CRC5 (1,2)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-________________________-___________________",
                valid       = "_----------------_________----------------___",
                value       = "000000000000010000000000000000011011011101000",
                crc_good    = "_______-__________---------_____-__________--"
            ),

            dict(
                # USB2 data with good CRC16 (1)
                width       = 16,
                polynomial  = 0b1000000000000101,
                initial     = 0b1111111111111111,
                residual    = 0b1000000000001101,
                reset       = "-______________________________________________________________________________________________",
                valid       = "_--------_--------_--------_--------_--------_--------_--------_--------_----------------______",
                value       = "00000000100110000000000000001000000000000000000000000000000001000000000001011101100101001000010",
                crc_good    = "__________________________________________________________________________________________-----"
            ),

            dict(
                # USB2 data with bad CRC16 (1)
                width       = 16,
                polynomial  = 0b1000000000000101,
                initial     = 0b1111111111111111,
                residual    = 0b1000000000001101,
                reset       = "-______________________________________________________________________________________________",
                valid       = "_--------_--------_--------_--------_--------_--------_--------_--------_----------------______",
                value       = "00000000100110000000000000001000000000010000000000000000000001000000000001011101100101001000010",
                crc_good    = "_______________________________________________________________________________________________"
            ),
        ]

        def stim(width, polynomial, initial, residual, reset, valid, value, crc_good):
            actual_crc_good = yield from send(reset, valid, value)
            self.assertEqual(actual_crc_good, crc_good)

        for i, vector in enumerate(test_vectors):
            with self.subTest(i=i, vector=vector):
                i_valid = Signal()
                i_data = Signal()
                i_reset = Signal()

                dut = RxCrcChecker(
                    vector["width"],
                    vector["polynomial"],
                    vector["initial"],
                    vector["residual"],
                    i_valid,
                    i_data,
                    i_reset)

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_crc_%d.vcd" % i)


class TestRxPacketDecode(TestCase):
    def test_pkt_decode(self):
        def send(valid, value):
            pid = []
            token_payload = []
            data_payload = []
            data = []
            pkt_good = []
            for i in range(len(valid)):
                yield i_valid.eq(valid[i] == '-')
                yield i_data.eq(value[i] == '1' or value[i] == 'B')
                yield i_se0.eq(value[i] == '_')
                yield i_bitstuff_error.eq(value[i] == 'B')
                yield

                o_pkt_start = yield dut.o_pkt_start
                o_pkt_pid = yield dut.o_pkt_pid
                o_pkt_token_payload = yield dut.o_pkt_token_payload
                o_pkt_data = yield dut.o_pkt_data
                o_pkt_data_put = yield dut.o_pkt_data_put
                o_pkt_good = yield dut.o_pkt_good
                o_pkt_end = yield dut.o_pkt_end

                if o_pkt_data_put:
                    data += [o_pkt_data]

                if o_pkt_end:
                    pid += [o_pkt_pid]
                    token_payload += [o_pkt_token_payload]
                    data_payload.append(data)
                    data = []
                    pkt_good += [o_pkt_good]

            return pid, token_payload, data_payload, pkt_good

        test_vectors = [
            dict(
                # USB2 SOF token
                valid           = "---------------------------------------",
                value           = "1100000001101001011000011011000010__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 SOF token - pipeline stalls
                valid           = "----------_--------_-----------_----------",
                value           = "1100000001_10100101_10000110110_00010__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 SOF token - eop dribble 1
                valid           = "----------_--------_-----------_-----_-----",
                value           = "1100000001_10100101_10000110110_00010_1__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 SOF token - eop dribble 6
                valid           = "----------_--------_-----------_-----_-----------",
                value           = "1100000001_10100101_10000110110_00010_111111__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 SOF token - bad pid
                valid           = "----------_--------_-----------_----------",
                value           = "1100000001_10100100_10000110110_00010__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 SOF token - bad crc5
                valid           = "----------_--------_-----------_----------",
                value           = "1100000001_10100101_10000110110_00011__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 SOF token - bitstuff error
                valid           = "----------_-----_____-_____--_-----------_----------",
                value           = "1100000001_10100_____B_____01_10000110110_00010__111",
                pid             = [0b0101],
                token_payload   = [865],
                data_payload    = [[]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 ACK handshake
                valid           = "----------_-------------",
                value           = "1100000001_01001011__111",
                pid             = [0b0010],
                token_payload   = [0],
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 ACK handshake - late bitstuff error
                valid           = "----------_-------------",
                value           = "1100000001_0100101B__111",
                pid             = [0b0010],
                token_payload   = [0],
                data_payload    = [[]],
                pkt_good        = [0]
            ),


            dict(
                # USB2 ACK handshake - pid error
                valid           = "----------_-------------",
                value           = "1100000001_01001111__111",
                pid             = [0b0010],
                token_payload   = [0],
                data_payload    = [[]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 ACK handshake - EOP dribble 1
                valid           = "----------_--------_-----",
                value           = "1100000001_01001011_1__111",
                pid             = [0b0010],
                token_payload   = [0],
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 ACK handshake - EOP dribble 6
                valid           = "----------_--------_-----------",
                value           = "1100000001_01001011_111111__111",
                pid             = [0b0010],
                token_payload   = [1792], # token payload doesn't matter in this test, but dribble triggers it
                data_payload    = [[]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 data with good CRC16 (1)
                valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------",
                value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001___1111",
                pid             = [0b0011],
                token_payload   = [1664], # token payload is a "don't care" for this test
                data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 data with good CRC16 - 1 eop dribble
                valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_-_-----",
                value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001_1___1111",
                pid             = [0b0011],
                token_payload   = [1664], # token payload is a "don't care" for this test
                data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
                pkt_good        = [1]
            ),

            dict(
                # USB2 data with good CRC16 - 6 eop dribble
                valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------_------",
                value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001_111111___1111",
                pid             = [0b0011],
                token_payload   = [1664], # token payload is a "don't care" for this test
                data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
                pkt_good        = [1]
            ),

            # TODO: need a better way to handle eop dribble with bitstuff error :(
            #dict(
            #    # USB2 data with good CRC16 - 1 eop dribble with bitstuff error
            #    valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_-_-----",
            #    value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001_B___1111",
            #    pid             = [0b0011],
            #    token_payload   = [1664], # token payload is a "don't care" for this test
            #    data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
            #    pkt_good        = [1]
            #),

            #dict(
            #    # USB2 data with good CRC16 - 6 eop dribble with bitstuff error
            #    valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------_------",
            #    value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001_11111B___1111",
            #    pid             = [0b0011],
            #    token_payload   = [1664], # token payload is a "don't care" for this test
            #    data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
            #    pkt_good        = [1]
            #),

            dict(
                # USB2 data with bad CRC16 (1)
                valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------",
                value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101011___1111",
                pid             = [0b0011],
                token_payload   = [1664], # token payload is a "don't care" for this test
                data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0xD4]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 data with late bitstuff error
                valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------",
                value       = "1100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_0010100B___1111",
                pid             = [0b0011],
                token_payload   = [1664], # token payload is a "don't care" for this test
                data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 data with bad pid
                valid       = "----------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------",
                value       = "1100000001_11000001_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001___1111",
                pid             = [0b0011],
                token_payload   = [1664], # token payload is a "don't care" for this test
                data_payload    = [[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
                pkt_good        = [0]
            ),

            dict(
                # USB2 SETUP and DATA
                valid           = "----------_--------_-----------_----------___---------------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_--------_------",
                value           = "1100000001_10110100_00000000000_01000__111___111111100000001_11000011_00000001_01100000_00000000_10000000_00000000_00000000_00000010_00000000_10111011_00101001___1111",
                pid             = [0b1101,  0b0011],
                token_payload   = [0,       1664],
                data_payload    = [[],      [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94]],
                pkt_good        = [1,       1]
            ),
        ]


        def stim(valid, value, pid, token_payload, data_payload, pkt_good):
            actual_pid, actual_token_payload, actual_data_payload, actual_pkt_good = yield from send(valid, value)
            self.assertEqual(actual_pid, pid)
            self.assertEqual(actual_token_payload, token_payload)
            self.assertEqual(actual_data_payload, data_payload)
            self.assertEqual(actual_pkt_good, pkt_good)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                i_valid = Signal()
                i_data = Signal()
                i_se0 = Signal()
                i_bitstuff_error = Signal()

                dut = RxPacketDecode(
                    i_valid,
                    i_data,
                    i_se0,
                    i_bitstuff_error)

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_decode_%d.vcd" % i)
            i += 1




class TestTxShifter(TestCase):
    def test_shifter(self):
        test_vectors = [
            dict(
                # basic shift out
                width   = 8,
                data    = [0b10011000],
                put     = "-______________",
                shift   = "_--------------",
                output  = " 00011001      "
            ),

            dict(
                # basic shift out - pipeline stall
                width   = 8,
                data    = [0b10011000],
                put     = "-_______________",
                shift   = "_-_-------------",
                output  = " 000011001      "
            ),

            dict(
                # basic shift out - pipeline stall
                width   = 8,
                data    = [0b10011000],
                put     = "-________________",
                shift   = "__-_-------------",
                output  = " 0000011001      "
            ),

            dict(
                # basic shift out - pipeline stall
                width   = 8,
                data    = [0b10011000],
                put     = "-________________________",
                shift   = "____-_------___-___------",
                output  = " 000000011001111         "
            ),

            dict(
                # basic shift out - pipeline stalls
                width   = 8,
                data    = [0b10011000],
                put     = "-______________________________________",
                shift   = "_-___-___-___-___-___-___-___-___------",
                output  = " 00000000011111111000000001111         "
            ),

            dict(
                # basic shift out multiple
                width   = 8,
                data    = [0b10011000, 0b11001011],
                put     = "-________-___________",
                shift   = "_--------_--------___",
                output  = " 00011001 11010011   "
            ),

            dict(
                # basic shift out multiple
                width   = 8,
                data    = [0b10011000, 0b11001011],
                put     = "-_________-___________",
                shift   = "_--------__--------___",
                output  = " 00011001  11010011   "
            ),
        ]

        def send(shift, put, data):
            output = ""
            for i in range(len(shift)):
                do_put = put[i] == '-'

                if do_put:
                    yield i_data.eq(data.pop(0))

                yield i_put.eq(do_put)
                yield i_shift.eq(shift[i] == '-')

                yield

                o_empty = yield dut.o_empty
                o_data = yield dut.o_data

                out = "%d%d" % (o_empty, o_data)

                output += {
                    "00" : "0",
                    "01" : "1",
                    "10" : " ",
                    "11" : " ",
                }[out]

            return output

        def stim(width, data, put, shift, output):
            actual_output = yield from send(shift, put, data)
            self.assertEqual(actual_output, output)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                i_put = Signal()
                i_shift = Signal()
                i_data = Signal(vector["width"])

                dut = TxShifter(vector["width"], i_put, i_shift, i_data)

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_tx_shifter_%d.vcd" % i)
                i += 1


def create_tester(dut_type, **def_args):
    def run(self, **test_args):
        name = self.id()

        self.inputs = dict()
        self.outputs = dict()
        self.params = set()
        self.dut_args = dict()

        # parse tester definition
        for key in def_args:
            if not key.startswith("i_") and not key.startswith("o_"):
                self.params.add(key)

        for key in def_args:
            if key.startswith("i_"):
                width = def_args[key][0]

                if isinstance(width, str):
                    width = test_args[width]

                self.inputs[key] = Signal(def_args[key][0])

            if key.startswith("o_"):
                self.outputs[key] = None

        # create dut
        for p in self.params:
            self.dut_args[p] = test_args[p]

        for i in self.inputs.keys():
            self.dut_args[i] = self.inputs[i]

        dut = dut_type(**self.dut_args)

        # gather outputs
        for o in self.outputs.keys():
            self.outputs[o] = getattr(dut, o)

        # calc num clocks
        clocks = 0
        for i in set(self.inputs.keys()) | set(self.outputs.keys()):
            if isinstance(test_args[i], str):
                clocks = max(clocks, len(test_args[i]))

        # decode stimulus
        def decode(c):
            try:
                return int(c, 16)
            except:
                pass

            if c == "-":
                return 1

            return 0

        # error message debug helper
        def to_waveform(sigs):
            output = ""

            for name in sigs.keys():
                output += "%20s: %s\n" % (name, sigs[name])

            return output



        actual_output = dict()

        # setup stimulus
        def stim():

            for signal_name in self.outputs.keys():
                actual_output[signal_name] = ""

            for i in range(clocks):
                for input_signal in self.inputs.keys():
                    yield self.inputs[input_signal].eq(decode(test_args[input_signal][i]))

                yield

                for output_signal in self.outputs.keys():
                    actual_value = yield self.outputs[output_signal]
                    actual_output[output_signal] += str(actual_value)


                    if isinstance(test_args[output_signal], tuple):
                        if test_args[output_signal][0][i] == '*':
                            expected_value = decode(test_args[output_signal][1].pop(0))

                    elif test_args[output_signal] is not None:
                        if test_args[output_signal][i] != ' ':
                            expected_value = decode(test_args[output_signal][i])
                            details = "\n"
                            if actual_value != expected_value:
                                details += "            Expected: %s\n" % (test_args[output_signal])
                                details += "                      " + (" " * i) + "^\n"
                                details += to_waveform(actual_output)
                            self.assertEqual(actual_value, expected_value, msg = ("%s:%s:%d" % (name, output_signal, i)) + details)


        # run simulation
        run_simulation(dut, stim(), vcd_name="vcd/%s.vcd" % name)

        return actual_output

    return run

def module_tester(dut_type, **def_args):
    def wrapper(class_type):
        class_type.do = create_tester(dut_type, **def_args)
        return class_type

    return wrapper


@module_tester(
    TxCrcGenerator,

    width       = None,
    polynomial  = None,
    initial     = None,

    i_reset     = (1,),
    i_data      = (1,),
    i_shift     = (1,),

    o_crc       = ("width",)
)
class TestTxCrcGenerator(TestCase):
    def test_token_crc5_zeroes(self):
        self.do(
            width       = 5,
            polynomial  = 0b00101,
            initial     = 0b11111,

            i_reset     = "-_______________",
            i_data      = "  00000000000   ",
            i_shift     = "__-----------___",
            o_crc       = "             222"
        )

    def test_token_crc5_zeroes_alt(self):
        self.do(
            width       = 5,
            polynomial  = 0b00101,
            initial     = 0b11111,

            i_reset     = "-______________",
            i_data      = " 00000000000   ",
            i_shift     = "_-----------___",
            o_crc       = "            222"
        )

    def test_token_crc5_nonzero(self):
        self.do(
            width       = 5,
            polynomial  = 0b00101,
            initial     = 0b11111,

            i_reset     = "-______________",
            i_data      = " 01100000011   ",
            i_shift     = "_-----------___",
            o_crc       = "            ccc"
        )

    def test_token_crc5_nonzero_stall(self):
        self.do(
            width       = 5,
            polynomial  = 0b00101,
            initial     = 0b11111,

            i_reset     = "-_____________________________",
            i_data      = " 0   1   111101110111000011   ",
            i_shift     = "_-___-___-___-___-___------___",
            o_crc       = "                           ccc"
        )

    def test_data_crc16_nonzero(self):
        self.do(
            width       = 16,
            polynomial  = 0b1000000000000101,
            initial     = 0b1111111111111111,

            i_reset     = "-________________________________________________________________________",
            i_data      = " 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 ",
            i_shift     = "_--------_--------_--------_--------_--------_--------_--------_--------_",
            o_crc       =("                                                                        *", [0x94dd])
        )




@module_tester(
    TxBitstuffer,

    i_valid     = (1,),
    i_oe        = (1,),
    i_data      = (1,),
    i_se0       = (1,),

    o_stall     = (1,),
    o_data      = (1,),
    o_se0       = (1,),
    o_oe        = (1,)
)
class TestTxBitstuffer(TestCase):
    def test_passthrough(self):
        self.do(
            i_valid = "_----------",
            i_oe    = "_--------__",
            i_data  = "_--___---__",
            i_se0   = "___________",

            o_stall = "___________",
            o_data  = "__--___---_",
            o_se0   = "___________",
            o_oe    = "__--------_",
        )

    def test_passthrough_se0(self):
        self.do(
            i_valid = "_----------",
            i_oe    = "_--------__",
            i_data  = "_--___---__",
            i_se0   = "____--_____",

            o_stall = "___________",
            o_data  = "__--___---_",
            o_se0   = "_____--____",
            o_oe    = "__--------_",
        )

    def test_bitstuff(self):
        self.do(
            i_valid = "_-----------",
            i_oe    = "_---------__",
            i_data  = "_---------__",
            i_se0   = "____________",

            o_stall = "_______-____",
            o_data  = "__------_--_",
            o_se0   = "____________",
            o_oe    = "__---------_",
        )

    def test_bitstuff_input_stall(self):
        self.do(
            i_valid = "_-___-___-___-___-___-___-___-___-__",
            i_oe    = "_-----------------------------------",
            i_data  = "_-----------------------------------",
            i_se0   = "____________________________________",

            o_stall = "______________________----__________",
            o_data  = "__------------------------____------",
            o_se0   = "____________________________________",
            o_oe    = "__----------------------------------",
        )

    def test_bitstuff_se0(self):
        self.do(
            i_valid = "_-----------__",
            i_oe    = "_-----------__",
            i_data  = "_---------____",
            i_se0   = "__________--__",

            o_stall = "_______-______",
            o_data  = "__------_--___",
            o_se0   = "___________---",
            o_oe    = "__------------",
        )

    def test_bitstuff_at_eop(self):
        self.do(
            i_valid = "_---------____",
            i_oe    = "_---------____",
            i_data  = "_-------______",
            i_se0   = "________--____",

            o_stall = "_______-______",
            o_data  = "__------______",
            o_se0   = "_________-----",
            o_oe    = "__------------",
        )

    def test_multi_bitstuff(self):
        self.do(
            i_valid = "_----------------",
            i_oe    = "_----------------",
            i_data  = "_----------------",
            i_se0   = "_________________",

            o_stall = "_______-______-__",
            o_data  = "__------_------_-",
            o_se0   = "_________________",
            o_oe    = "__---------------",
        )



@module_tester(
    TxNrziEncoder,

    i_valid     = (1,),
    i_oe        = (1,),
    i_data      = (1,),
    i_se0       = (1,),

    o_usbp      = (1,),
    o_usbn      = (1,),
    o_oe        = (1,)
)
class TestTxNrziEncoder(TestCase):
    def test_setup_token(self):
        self.do(
            i_valid = "_--------------------------------------",
            i_oe    = "_----------------------------------____",
            i_data  = "_0000000110110100000000000000100000____",
            i_se0   = "_________________________________--____",

            o_oe    = "___-----------------------------------_",
            o_usbp  = "   _-_-_-___---__-_-_-_-_-_-_-__-_-__- ",
            o_usbn  = "   -_-_-_---___--_-_-_-_-_-_-_--_-____ ",
        )


def encode_data(data):
    """
    Converts array of 8-bit ints into string of 0s and 1s.
    """
    output = ""

    for b in data:
        output += ("{0:08b}".format(b))[::-1]

    return output


# width=5 poly=0x05 init=0x1f refin=true refout=true xorout=0x1f check=0x19 residue=0x06 name="CRC-5/USB"
def crc5_token(addr, ep):
    """
    >>> hex(crc5_token(0, 0))
    '0x2'
    >>> hex(crc5_token(92, 0))
    '0x1c'
    >>> hex(crc5_token(3, 0))
    '0xa'
    >>> hex(crc5_token(56, 4))
    '0xb'
    """
    reg = crc.CrcRegister(crc.CRC5_USB)
    reg.takeWord(addr, 7)
    reg.takeWord(ep, 4)
    return reg.getFinalValue()


def crc5_sof(v):
    """
    >>> hex(crc5_sof(1429))
    '0x1'
    >>> hex(crc5_sof(1013))
    '0x5'
    """
    reg = crc.CrcRegister(crc.CRC5_USB)
    reg.takeWord(v, 11)
    return reg.getFinalValue()


def crc16(input_data):
    # width=16 poly=0x8005 init=0xffff refin=true refout=true xorout=0xffff check=0xb4c8 residue=0xb001 name="CRC-16/USB"
    # CRC appended low byte first.
    reg = crc.CrcRegister(crc.CRC16_USB)
    for d in input_data:
        assert d < 256, input_data
        reg.takeWord(d, 8)
    crc16 = reg.getFinalValue()
    return [crc16 & 0xff, (crc16 >> 8) & 0xff]


def nrzi(data, clock_width=4):
    """
    Converts string of 0s and 1s into NRZI encoded string.
    """
    def toggle_state(state):
        if state == 'J':
            return 'K'
        if state == 'K':
            return 'J'
        return state

    state = "K"
    output = ""

    stuffed = []
    i = 0
    for bit in data:
        stuffed.append(bit)
        if bit == '1':
            i += 1
        else:
            i = 0
        if i > 5:
            stuffed.append('0')
            i = 0

    for bit in stuffed:
        # only toggle the state on '0'
        if bit == '0':
            state = toggle_state(state)
        elif bit == '1':
            pass
        elif bit in "jk_":
            state = bit.upper()
        else:
            assert False, "Unknown bit %s in %r" % (bit, data)

        output += (state * clock_width)

    return output


def line(data):
    oe = ""
    usbp = ""
    usbn = ""

    for bit in data:
        oe += "-"
        if bit == "j":
            usbp += "-"
            usbn += "_"
        if bit == "k":
            usbp += "_"
            usbn += "-"
        if bit == "_":
            usbp += "_"
            usbn += "_"

    return (oe, usbp, usbn)



def sync():
    return "kjkjkjkk"


def encode_pid(value):
    return encode_data([value | ((0b1111 ^ value) << 4)])


def eop():
    return "__j"


def idle():
    return r"\s+"


class TestUsbFsTx_longer(TestCase):
    def do(self, clocks, pid, token_payload, data, expected_output):
        self.output = ""
        name = self.id()

        # create dut
        i_bit_strobe = Signal(1)
        i_pkt_start = Signal(1)
        i_pid = Signal(4)
        i_token_payload = Signal(11)
        i_data_valid = Signal(1)
        i_data_payload = Signal(8)

        dut = UsbFsTx(i_bit_strobe, i_pkt_start, i_pid, i_token_payload, i_data_valid, i_data_payload)

        def clock():
            yield i_data_valid.eq(len(data) > 0)
            if len(data) > 0:
                yield i_data_payload.eq(data[0])
            else:
                yield i_data_payload.eq(0)

            yield

            o_data_get = yield dut.o_data_get
            if o_data_get:
                data.pop(0)

            oe = yield dut.o_oe
            usbp = yield dut.o_usbp
            usbn = yield dut.o_usbn

            if oe == 0:
                self.output += " "
            else:
                if usbp == 0 and usbn == 0:
                    self.output += "_"
                elif usbp == 1 and usbn == 0:
                    self.output += "j"
                elif usbp == 0 and usbn == 1:
                    self.output += "k"
                else:
                    self.output += "!"

        # setup stimulus
        def stim():
            # initiate packet transmission
            yield i_pid.eq(pid)
            yield i_token_payload.eq(token_payload)
            yield i_pkt_start.eq(1)

            yield from clock()

            yield i_pid.eq(0)
            yield i_token_payload.eq(0)
            yield i_pkt_start.eq(0)

            # pump the clock and collect output
            for i in range(clocks):
                yield i_bit_strobe.eq(1)

                yield from clock()

                yield i_bit_strobe.eq(0)

                yield from clock()
                yield from clock()
                yield from clock()

            import re
            m = re.fullmatch(idle() + expected_output + idle(), self.output)
            if m:
                pass
            else:
                raise AssertionError("Packet not found:\n    %s\n    %s" % (expected_output, self.output))


        # run simulation
        run_simulation(dut, stim(), vcd_name="vcd/%s.vcd" % name)


    def test_ack_handshake(self):
        self.do(
            clocks          = 100,
            pid             = PID.ACK,
            token_payload   = 0,
            data            = [],
            expected_output = nrzi(sync() + encode_pid(PID.ACK) + eop())
        )

    def test_empty_data(self):
        self.do(
            clocks          = 100,
            pid             = PID.DATA0,
            token_payload   = 0,
            data            = [],
            expected_output = nrzi(sync() + encode_pid(PID.DATA0) + encode_data([0x00, 0x00]) + eop())
        )

    def test_setup_data(self):
        payload = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00]

        self.do(
            clocks          = 200,
            pid             = PID.SETUP,
            token_payload   = 0,
            data            = payload,
            expected_output = nrzi(sync() + encode_pid(PID.SETUP) + encode_data(payload + crc16(payload)) + eop())
        )

    def test_setup_data_bitstuff(self):
        payload = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x3F]
        self.do(
            clocks          = 200,
            pid             = PID.SETUP,
            token_payload   = 0,
            data            = payload,
            expected_output = nrzi(sync() + encode_pid(PID.SETUP) + encode_data([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40]) + "111111000" + encode_data(crc16(payload)) + eop())
        )


class TestIoBuf(Module):
    def __init__(self):
        self.usb_pullup = Signal()

        self.usb_p = Signal()
        self.usb_n = Signal()

        self.usb_tx_en = Signal()
        self.usb_p_tx = Signal()
        self.usb_n_tx = Signal()

        self.usb_p_rx = Signal()
        self.usb_n_rx = Signal()

        self.usb_p_rx_io = Signal()
        self.usb_n_rx_io = Signal()

        self.comb += [
            If(self.usb_tx_en,
                self.usb_p_rx.eq(0b1),
                self.usb_n_rx.eq(0b0)
            ).Else(
                self.usb_p_rx.eq(self.usb_p_rx_io),
                self.usb_n_rx.eq(self.usb_n_rx_io)
            ),
        ]
        self.comb += [
            If(self.usb_tx_en,
                self.usb_p.eq(self.usb_p_tx),
                self.usb_n.eq(self.usb_n_tx),
            ).Else(
                self.usb_p.eq(self.usb_p_rx),
                self.usb_n.eq(self.usb_n_rx),
            ),
        ]

    def recv(self, v):
        if v == '0' or v == '_':
            # SE0 - both lines pulled low
            yield self.usb_p_rx_io.eq(0)
            yield self.usb_n_rx_io.eq(0)
        elif v == '1':
            # SE1 - illegal, should never occur
            yield self.usb_p_rx_io.eq(1)
            yield self.usb_n_rx_io.eq(1)
        elif v == '-' or v == 'I':
            # Idle
            yield self.usb_p_rx_io.eq(1)
            yield self.usb_n_rx_io.eq(0)
        elif v == 'J':
            yield self.usb_p_rx_io.eq(1)
            yield self.usb_n_rx_io.eq(0)
        elif v == 'K':
            yield self.usb_p_rx_io.eq(0)
            yield self.usb_n_rx_io.eq(1)
        else:
            assert False, "Unknown value: %s" % v

    def current(self):
        usb_p = yield self.usb_p
        usb_n = yield self.usb_n
        values = (usb_p, usb_n)

        if values == (0, 0):
            return '_'
        elif values == (1, 1):
            return '1'
        elif values == (1, 0):
            return 'J'
        elif values == (0, 1):
            return 'K'
        else:
            assert False, values


def token_packet(pid, addr, endp):
    """Create a token packet for testing.

    sync, pid, addr (7bit), endp(4bit), crc5(5bit), eop

    >>> token_packet(PID.SETUP, 0x0, 0x0)
    '101101000000000000001000'

     PPPPPPPP                 - 8 bits - PID
             AAAAAAA          - 7 bits - ADDR
                    EEEE      - 4 bits - EP
                        CCCCC - 5 bits - CRC

    >>> token_packet(PID.IN, 0x3, 0x0) # 0x0A
    '100101101100000000001010'

    >>> token_packet(PID.OUT, 0x3a, 0xa)
    '100001110101110010111100'

    >>> token_packet(PID.SETUP, 0x70, 0xa)
    '101101000000111010110101'

    >>> token_packet(PID.SETUP, 40, 2)
    '101101000001010010000011'

    >>> token_packet(PID.SETUP, 28, 2)
    '101101000011100010001001'

     PPPPPPPP                 - 8 bits - PID
             AAAAAAA          - 7 bits - ADDR
                    EEEE      - 4 bits - EP
                        CCCCC - 5 bits - CRC
    """
    assert addr < 128, addr
    assert endp < 2**4, endp
    assert pid in (PID.OUT, PID.IN, PID.SETUP), pid
    token = encode_pid(pid)
    token += "{0:07b}".format(addr)[::-1]                   # 7 bits address
    token += "{0:04b}".format(endp)[::-1]                   # 4 bits endpoint
    token += "{0:05b}".format(crc5_token(addr, endp))[::-1] # 5 bits CRC5
    assert len(token) == 24, token
    return token


def data_packet(pid, payload):
    """Create a data packet for testing.

    sync, pid, data, crc16, eop
    FIXME: data should be multiples of 8?

    >>> data_packet(PID.DATA0, [0x80, 0x06, 0x03, 0x03, 0x09, 0x04, 0x00, 0x02])
    '1100001100000001011000001100000011000000100100000010000000000000010000000110101011011100'

    >>> data_packet(PID.DATA1, [])
    '110100100000000000000000'

    """
    assert pid in (PID.DATA0, PID.DATA1), pid
    payload = list(payload)
    return encode_pid(pid) + encode_data(payload + crc16(payload))


def handshake_packet(pid):
    """ Create a handshake packet for testing.

    sync, pid, eop
    ack / nak / stall / nyet (high speed only)

    >>> handshake_packet(PID.ACK)
    '01001011'
    >>> handshake_packet(PID.NAK)
    '01011010'
    """
    assert pid in (PID.ACK, PID.NAK, PID.STALL), pid
    return encode_pid(pid)


def sof_packet(frame):
    """Create a SOF packet for testing.

    sync, pid, frame no (11bits), crc5(5bits), eop

    >>> sof_packet(1)
    '101001010000000010111100'

    >>> sof_packet(100)
    '101001010011000011111001'

    >>> sof_packet(257)
    '101001010000010000011100'

    >>> sof_packet(1429)
    '101001010100110110000101'

    >>> sof_packet(2**11 - 2)
    '101001011111111111101011'
    """
    assert frame < 2**11, (frame, '<', 2**11)
    data = [frame >> 3, (frame & 0b111) << 5]
    data[-1] = data[-1] | crc5_sof(frame)
    return encode_pid(PID.SOF) + encode_data(data)


def pp_packet(p):
    """
    >>> print(pp_packet(wrap_packet(token_packet(PID.SETUP, 0, 0))))
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    KKKK 1 PID (PID.SETUP)
    JJJJ 2 PID
    JJJJ 3 PID
    JJJJ 4 PID
    KKKK 5 PID
    KKKK 6 PID
    JJJJ 7 PID
    KKKK 8 PID
    JJJJ 1 Address
    KKKK 2 Address
    JJJJ 3 Address
    KKKK 4 Address
    JJJJ 5 Address
    KKKK 6 Address
    JJJJ 7 Address
    KKKK 1 Endpoint
    JJJJ 2 Endpoint
    KKKK 3 Endpoint
    JJJJ 4 Endpoint
    KKKK 1 CRC5
    KKKK 2 CRC5
    JJJJ 3 CRC5
    KKKK 4 CRC5
    JJJJ 5 CRC5
    ____ SE0
    ____ SE0
    JJJJ END
    >>> print(pp_packet(wrap_packet(data_packet(PID.DATA0, [5, 6]))))
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    KKKK 1 PID (PID.DATA0)
    KKKK 2 PID
    JJJJ 3 PID
    KKKK 4 PID
    JJJJ 5 PID
    KKKK 6 PID
    KKKK 7 PID
    KKKK 8 PID
    KKKK
    JJJJ
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    JJJJ
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    KKKK  1 CRC16
    JJJJ  2 CRC16
    JJJJ  3 CRC16
    JJJJ  4 CRC16
    JJJJ  5 CRC16
    JJJJ  6 CRC16
    JJJJ  7 CRC16
    KKKK  8 CRC16
    KKKK  9 CRC16
    JJJJ 10 CRC16
    JJJJ 11 CRC16
    JJJJ 12 CRC16
    JJJJ 13 CRC16
    KKKK 14 CRC16
    JJJJ 15 CRC16
    KKKK SE0
    ____ SE0
    ____ END

    >>> # Requires bit stuffing!
    >>> print(pp_packet(wrap_packet(data_packet(PID.DATA0, [0x1]))))
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    KKKK 1 PID (PID.DATA0)
    KKKK 2 PID
    JJJJ 3 PID
    KKKK 4 PID
    JJJJ 5 PID
    KKKK 6 PID
    KKKK 7 PID
    KKKK 8 PID
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    JJJJ
    KKKK  1 CRC16
    JJJJ  2 CRC16
    KKKK  3 CRC16
    JJJJ  4 CRC16
    KKKK  5 CRC16
    JJJJ  6 CRC16
    JJJJ  7 CRC16
    JJJJ  8 CRC16
    JJJJ  9 CRC16
    JJJJ 10 CRC16
    JJJJ 11 CRC16
    JJJJ 12 CRC16
    KKKK 13 CRC16
    KKKK 14 CRC16
    KKKK 15 CRC16
    JJJJ SE0
    ____ SE0
    ____ END

    """
    output = []
    chunks = [p[i:i+4] for i in range(0, len(p), 4)]
    for i in range(1, 9):
        if chunks:
            output.extend([chunks.pop(0), ' %i Sync\n' % i])

    pid_type = None
    for i in range(1, 9):
        if chunks:
            if i == 1:
                pid_encoded = "".join(chunks[:8])
                for p in PID:
                    if nrzi(encode_pid(p.value)) == pid_encoded:
                        pid_type = p
                output.extend([chunks.pop(0), ' %i PID (%s)\n' % (i, pid_type)])
            else:
                output.extend([chunks.pop(0), ' %i PID\n' % i])

    if pid_type == PID.DATA0 or pid_type == PID.DATA1:
        while len(chunks) > 16+3:
            output.extend([chunks.pop(0), '\n'])
        for i in range(1, 16):
            if chunks:
                output.extend([chunks.pop(0), ' %2i CRC16\n' % i])
    else:
        for i in range(1, 8):
            if chunks:
                output.extend([chunks.pop(0), ' %i Address\n' % i])
        for i in range(1, 5):
            if chunks:
                output.extend([chunks.pop(0), ' %i Endpoint\n' % i])
        for i in range(1, 6):
            if chunks:
                output.extend([chunks.pop(0), ' %i CRC5\n' % i])
        while len(chunks) > 3:
            output.extend([chunks.pop(0), '\n'])

    if chunks:
        output.extend([chunks.pop(0), ' SE0\n'])
    if chunks:
        output.extend([chunks.pop(0), ' SE0\n'])
    if chunks:
        output.extend([chunks.pop(0), ' END'])

    return "".join(output)


def wrap_packet(data):
    """Add the sync + eop sections and do nrzi encoding.

    >>> wrap_packet(handshake_packet(PID.ACK))
    'KKKKJJJJKKKKJJJJKKKKJJJJKKKKKKKKJJJJJJJJKKKKJJJJJJJJKKKKKKKKKKKK________JJJJ'

    """
    return nrzi(sync() + data + eop())


def squash_packet(data):
    return 'I'*8 + 'I'*len(data) + 'I'*30


# one out transaction
# >token, >dataX, <ack

# one in transaction
# >token, <dataX, <ack

# one setup transaction
# >token, >data0, <ack

# setup stage (pid:setup, pid:data0 - 8 bytes, pid:ack)
# [data stage (pid:in+pid:data1, pid:in +pid:data0, ...)]
# status stage (pid:out, pid:data1 - 0 bytes)


# DATA0 and DATA1 PIDs are used in Low and Full speed links as part of an error-checking system.
# When used, all data packets on a particular endpoint use an alternating DATA0
# / DATA1 so that the endpoint knows if a received packet is the one it is
# expecting.
# If it is not it will still acknowledge (ACK) the packet as it is correctly
# received, but will then discard the data, assuming that it has been
# re-sent because the host missed seeing the ACK the first time it sent the
# data packet.


# 1) reset,
#
# 2) The host will now send a request to endpoint 0 of device address 0 to find
#    out its maximum packet size. It can discover this by using the Get
#    Descriptor (Device) command. This request is one which the device must
#    respond to even on address 0.
#
# 3) Then sends a Set Address request, with a unique address to the device at
#    address 0. After the request is completed, the device assumes the new
#    address.
#
#

def grouper(n, iterable, pad=None):
    """Group iterable into multiples of n (with optional padding).

    >>> list(grouper(3, 'abcdefg', 'x'))
    [('a', 'b', 'c'), ('d', 'e', 'f'), ('g', 'x', 'x')]

    """
    return zip_longest(*[iter(iterable)]*n, fillvalue=pad)


class CommonUsbTestCase(TestCase):
    maxDiff=None

    def ep_print(self, epaddr, msg, *args):
        print("ep(%i, %s): %s" % (
            EndpointType.epnum(epaddr),
            EndpointType.epdir(epaddr).name,
            msg) % args)

    def idle(self, cycles=10):
        yield self.packet_idle.eq(1)
        yield from self.dut.iobuf.recv('I')
        for i in range(0, cycles):
            yield
        yield self.packet_idle.eq(0)

    # Host->Device
    def _send_packet(self, packet):
        """Send a USB packet."""
        #print("_send_packet(", packet, ")")
        packet = wrap_packet(packet)
        for v in packet:
            yield from self._update_internal_signals()
            yield from self.dut.iobuf.recv(v)
            yield
        yield from self._update_internal_signals()
        yield

    def send_token_packet(self, pid, addr, epaddr):
        epnum = EndpointType.epnum(epaddr)
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(token_packet(pid, addr, epnum))
        yield self.packet_h2d.eq(0)

    def send_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(data_packet(pid, data))
        yield self.packet_h2d.eq(0)

    def send_ack(self):
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(handshake_packet(PID.ACK))
        yield self.packet_h2d.eq(0)

    def send_nak(self):
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(handshake_packet(PID.NAK))
        yield self.packet_h2d.eq(0)

    # Device->Host
    def expect_packet(self, packet, msg=None):
        """Except to receive the following USB packet."""
        yield self.packet_d2h.eq(1)

        # Wait for transmission to happen
        yield from self.dut.iobuf.recv('I')
        tx = 0
        for i in range(0, 100):
            yield from self._update_internal_signals()
            tx = yield self.dut.iobuf.usb_tx_en
            if tx:
                break
            yield
        self.assertTrue(tx, "No packet started, "+msg)

        # Read in the packet data
        result = ""
        for i in range(0, 2048):
            yield from self._update_internal_signals()

            result += yield from self.iobuf.current()
            yield
            tx = yield self.dut.iobuf.usb_tx_en
            if not tx:
                break
        self.assertFalse(tx, "Packet didn't finish, "+msg)
        yield self.packet_d2h.eq(0)

        # Check the packet received matches
        expected = pp_packet(wrap_packet(packet))
        actual = pp_packet(result)

        self.assertMultiLineEqual(expected, actual, msg)

    # No expect_token_packet, as the host is the only one who generates tokens.

    def expect_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(data_packet(pid, data), "Expected %s packet with %r" % (pid.name, data))
        yield self.packet_d2h.eq(0)

    def expect_ack(self):
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(handshake_packet(PID.ACK), "Expected ACK packet.")
        yield self.packet_d2h.eq(0)

    def expect_nak(self):
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(handshake_packet(PID.NAK), "Expected NAK packet.")
        yield self.packet_d2h.eq(0)

    def expect_stall(self):
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(handshake_packet(PID.STALL), "Expected STALL packet.")
        yield self.packet_d2h.eq(0)

    def expect_last_tok(self, epaddr, value):
        if False:
            yield

    def check_pending(self, epaddr):
        # Check no pending packets
        self.assertFalse((yield from self.pending(epaddr)))

    def check_pending_and_response(self, epaddr):
        yield from self.check_pending(epaddr)
        # Check we are going to ack the packets
        self.assertEqual((yield from self.response(epaddr)), EndpointResponse.ACK)

    # Full transactions
    # ->token  ->token
    # <-data   ->data
    # ->ack    <-ack

    # Host to Device
    # ->setup
    # ->data0[...]
    # <-ack
    def transaction_setup(self, addr, data):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        yield from self.send_token_packet(PID.SETUP, addr, epaddr_out)
        yield from self.send_data_packet(PID.DATA0, data)
        yield from self.expect_ack()
        yield from self.expect_data(epaddr_out, data)
        yield from self.clear_pending(epaddr_out)

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr_out)))

        # Check the token is set correctly
        yield from self.expect_last_tok(epaddr_out, 0b11)

        # Check the in/out endpoint is reset to NAK
        self.assertEqual((yield from self.response(epaddr_out)), EndpointResponse.NAK)
        self.assertEqual((yield from self.response(epaddr_in)), EndpointResponse.NAK)

    # Host to Device
    # ->out
    # ->data0[...]
    # <-ack
    # ->out
    # ->data1[...]
    # <-ack
    # ....
    def transaction_data_out(self, addr, epaddr, data, chunk_size=8):
        yield from self.check_pending_and_response(epaddr)

        datax = PID.DATA0
        for i, chunk in enumerate(grouper(chunk_size, data, pad=0)):
            self.assertFalse((yield from self.pending(epaddr)))
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(datax, chunk)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, chunk)
            yield from self.clear_pending(epaddr)

            yield from self.expect_last_tok(epaddr, 0b00)
            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Host to Device
    # ->out
    # ->data1[]
    # <-ack
    def transaction_status_out(self, addr, epaddr):
        assert EndpointType.epdir(epaddr) == EndpointType.OUT
        yield from self.check_pending_and_response(epaddr)

        yield from self.send_token_packet(PID.OUT, addr, epaddr)
        yield from self.send_data_packet(PID.DATA1, [])
        yield from self.expect_ack()
        yield from self.expect_data(epaddr, [])
        yield from self.clear_pending(epaddr)

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Device to Host
    # ->in
    # <-data0[...]
    # ->ack
    # ->in
    # <-data1[...]
    # ->ack
    # ....
    def transaction_data_in(self, addr, epaddr, data, chunk_size=8, dtb=PID.DATA1):
        assert EndpointType.epdir(epaddr) == EndpointType.IN

        datax = dtb
        for i, chunk in enumerate(grouper(chunk_size, data, pad=0)):
            yield from self.check_pending_and_response(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield from self.set_data(epaddr, chunk)
            yield from self.set_response(epaddr, EndpointResponse.ACK)

            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(datax, chunk)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

            yield from self.expect_last_tok(epaddr, 0b10)
            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Device to Host
    # ->in
    # <-data1[]
    # ->ack
    def transaction_status_in(self, addr, epaddr):
        assert EndpointType.epdir(epaddr) == EndpointType.IN
        yield from self.check_pending_and_response(epaddr)

        yield from self.set_data(epaddr, [])
        yield from self.send_token_packet(PID.IN, addr, epaddr)
        yield from self.expect_data_packet(PID.DATA1, [])
        yield from self.send_ack()
        yield from self.clear_pending(epaddr)

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Full control transfer
    ########################
    def control_transfer_in(self, addr, setup_data, descriptor_data):
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

        # Setup stage
        yield from self.transaction_setup(addr, setup_data)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

        # Data stage
        yield from self.set_response(epaddr_in, EndpointResponse.ACK)
        yield from self.transaction_data_in(addr, epaddr_in, descriptor_data)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

        # Status stage
        yield from self.set_response(epaddr_out, EndpointResponse.ACK)
        yield from self.transaction_status_out(addr, epaddr_out)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

    def control_transfer_out(self, addr, setup_data, descriptor_data):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
        # Setup stage
        yield from self.transaction_setup(addr, setup_data)
        # Data stage
        yield from self.set_response(epaddr_out, EndpointResponse.ACK)
        yield from self.transaction_data_out(addr, epaddr_out, descriptor_data)
        # Status stage
        yield from self.set_response(epaddr_in, EndpointResponse.ACK)
        yield from self.transaction_status_in(addr, epaddr_in)

    ######################################################################
    ######################################################################
    def run_sim(self, stim):
        raise NotImplementedError

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        raise NotImplementedError

    def clear_pending(self, epaddr):
        raise NotImplementedError

    def pending(self, epaddr):
        raise NotImplementedError

    # Endpoint state -----------------------
    def response(self, epaddr):
        raise NotImplementedError

    def set_response(self, epaddr, v):
        raise NotImplementedError

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        raise NotImplementedError

    def expect_data(self, epaddr, data):
        raise NotImplementedError

    ######################################################################
    ######################################################################

    def test_control_setup(self):
        def stim():
            #   012345   0123
            # 0b011100 0b1000
            yield from self.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
        self.run_sim(stim)

    def test_control_setup_clears_stall(self):
        def stim():
            addr = 28
            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            yield from self.clear_pending(epaddr_out)
            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA0, d[:4])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr_out, d[:4])

            yield from self.set_response(epaddr_out, EndpointResponse.STALL)

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA0, d[4:])
            yield from self.expect_stall()

            yield from self.send_token_packet(PID.SETUP, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_ack()

            yield
            respond = yield from self.response(epaddr_out)
            self.assertEqual(respond, EndpointResponse.NAK)
            yield

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA0, d[:4])
            yield from self.expect_nak()

        self.run_sim(stim)

    def test_control_transfer_in(self):
        def stim():
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
            yield

            yield from self.control_transfer_in(
                20,
                # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
                [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
                # 12 byte descriptor, max packet size 8 bytes
                [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                 0x08, 0x09, 0x0A, 0x0B],
            )
        self.run_sim(stim)

    def test_control_transfer_in_nak_data(self):
        def stim():
            addr = 22
            # Get descriptor, Index 0, Type 03, LangId 0000, wLength 64
            setup_data = [0x80, 0x06, 0x00, 0x03, 0x00, 0x00, 0x40, 0x00]
            in_data = [0x04, 0x03, 0x09, 0x04]

            epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(epaddr_in)

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # -----------
            yield from self.set_response(epaddr_in, EndpointResponse.NAK)
            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_nak()

            yield from self.set_data(epaddr_in, in_data)
            yield from self.set_response(epaddr_in, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_data_packet(PID.DATA1, in_data)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr_in)

        self.run_sim(stim)

    def test_control_transfer_in_nak_status(self):
        def stim():
            addr = 20
            setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
            out_data = [0x00, 0x01]

            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
            epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(epaddr_out)
            yield from self.clear_pending(epaddr_in)

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # ----------
            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield from self.transaction_data_out(addr, epaddr_out, out_data)

            # Status stage
            # ----------
            yield from self.set_response(epaddr_in, EndpointResponse.NAK)

            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_nak()

            yield from self.set_response(epaddr_in, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_data_packet(PID.DATA1, [])
            yield from self.send_ack()
            yield from self.clear_pending(epaddr_in)

        self.run_sim(stim)

    def test_control_transfer_out(self):
        def stim():
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
            yield

            yield from self.control_transfer_out(
                20,
                # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
                [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
                # 12 byte descriptor, max packet size 8 bytes
                [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                 0x08, 0x09, 0x0A, 0x0B],
            )
        self.run_sim(stim)

    def test_control_transfer_out_nak_data(self):
        def stim():
            addr = 20
            setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
            out_data = [
                0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                0x08, 0x09, 0x0A, 0x0B,
            ]

            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
            yield from self.clear_pending(epaddr_out)

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # ----------
            yield from self.set_response(epaddr_out, EndpointResponse.NAK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, out_data)
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, out_data)
            yield from self.expect_nak()

            #for i in range(200):
            #    yield

            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, out_data)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr_out, out_data)
            yield from self.clear_pending(epaddr_out)


        self.run_sim(stim)

    def test_control_transfer_out_nak_status(self):
        def stim():
            addr = 20
            setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
            descriptor_data = [
                0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                0x08, 0x09, 0x0A, 0x0B,
            ]

            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
            epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(epaddr_out)
            yield from self.clear_pending(epaddr_in)
            yield

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # ----------
            yield from self.set_response(epaddr_in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, epaddr_in, descriptor_data)

            # Status stage
            # ----------
            yield from self.set_response(epaddr_out, EndpointResponse.NAK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, [])
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, [])
            yield from self.expect_nak()

            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, [])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr_out, [])
            yield from self.clear_pending(epaddr_out)

        self.run_sim(stim)

    def test_in_transfer(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(1, EndpointType.IN)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            yield from self.set_data(epaddr, d[:4])
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA1, d[:4])
            yield from self.send_ack()

            self.assertTrue((yield from self.pending(epaddr)))
            yield from self.set_data(epaddr, d[4:])
            yield from self.clear_pending(epaddr)

            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA0, d[4:])
            yield from self.send_ack()

        self.run_sim(stim)

    def test_data_in_byte_1(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)

            d1 = [0x1]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_data_in_byte_2(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)

            d1 = [0x2]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_data_in_byte_a(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)

            d1 = [0xa]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_setup_clears_data_toggle_bit(self):
        def stim():
            addr = 28

            ep0in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(ep0in)
            yield from self.set_response(ep0in, EndpointResponse.NAK)

            ep0out = EndpointType.epaddr(0, EndpointType.OUT)
            yield from self.clear_pending(ep0out)
            yield from self.set_response(ep0out, EndpointResponse.NAK)
            yield

            # Setup stage
            yield from self.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])

            dtbi = yield from self.dtb(ep0in)
            self.assertTrue(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertTrue(dtbo)

            # Data stage
            yield from self.set_response(ep0in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, ep0in, [0x1])

            dtbi = yield from self.dtb(ep0in)
            self.assertFalse(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertTrue(dtbo)

            # Status stage
            yield from self.set_response(ep0out, EndpointResponse.ACK)
            yield from self.transaction_status_out(addr, ep0out)

            dtbi = yield from self.dtb(ep0in)
            self.assertFalse(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertFalse(dtbo)

            # Data transfer
            yield from self.set_response(ep0in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, ep0in, [0x1], dtb=PID.DATA0)

            dtbi = yield from self.dtb(ep0in)
            self.assertTrue(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertFalse(dtbo)

            # Data transfer
            yield from self.set_response(ep0in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, ep0in, [0x2], dtb=PID.DATA1)

            dtbi = yield from self.dtb(ep0in)
            self.assertFalse(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertFalse(dtbo)

            # New setup stage should reset dtb
            yield from self.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])

            dtbi = yield from self.dtb(ep0in)
            self.assertTrue(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertTrue(dtbo)

        self.run_sim(stim)

    def test_data_toggle_bit_multiple_endpoints(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)
            ep2 = EndpointType.epaddr(2, EndpointType.IN)
            yield from self.clear_pending(ep2)
            yield from self.set_response(ep2, EndpointResponse.NAK)
            yield

            d1 = [0x1]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

            d2 = [0x2]
            yield from self.set_data(ep2, d2)
            yield from self.set_response(ep2, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep2)
            yield from self.expect_data_packet(PID.DATA1, d2)
            yield from self.send_ack()
            yield from self.clear_pending(ep2)

            d3 = [0x3]
            yield from self.set_data(ep2, d3)
            yield from self.set_response(ep2, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep2)
            yield from self.expect_data_packet(PID.DATA0, d3)
            yield from self.send_ack()
            yield from self.clear_pending(ep2)

            d4 = [0x5]
            yield from self.set_data(ep1, d4)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA0, d4)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_in_transfer_nak(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(1, EndpointType.IN)

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            # Device NAK the PID.IN token packet
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()

            # Device NAK the PID.IN token packet a second time
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()

            d1 = [0x1, 0x2, 0x3, 0x4]
            yield from self.set_data(epaddr, d1)
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

            # Have data but was asked to NAK
            d2 = [0x5, 0x6, 0x7, 0x8]
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield from self.set_data(epaddr, d2)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()

            # Actually send the data now
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA0, d2)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_in_stall(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(1, EndpointType.IN)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            # While pending, set stall
            self.assertTrue((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.set_data(epaddr, d[:4])
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()
            yield from self.clear_pending(epaddr)

            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA1, d[:4])
            yield from self.send_ack()
            yield from self.set_data(epaddr, d[4:])
            yield from self.clear_pending(epaddr)

            # While not pending, set stall
            self.assertFalse((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA0, d[4:])
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_out_transfer(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(2, EndpointType.OUT)

            d = [0x41, 0x01]

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d)

            # Should nak until pending is cleared
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()

            # Make sure no extra data turned up
            #yield from self.expect_data(epaddr, [])

            yield from self.clear_pending(epaddr)

            d2 = [0x41, 0x02]
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d2)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d2)
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_out_transfer_nak(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(2, EndpointType.OUT)

            d = [0x41, 0x01]

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            # First nak
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()
            pending = yield from self.pending(epaddr)
            self.assertFalse(pending)

            # Second nak
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()
            pending = yield from self.pending(epaddr)
            self.assertFalse(pending)

            # Third attempt succeeds
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d)
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_out_stall(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(2, EndpointType.OUT)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            # While pending, set stall
            self.assertTrue((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d[:4])
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d[:4])
            yield from self.expect_nak()
            yield from self.clear_pending(epaddr)

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d[:4])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d[:4])
            yield from self.clear_pending(epaddr)

            # While not pending, set stall
            self.assertFalse((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA0, d[4:])
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA0, d[4:])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d[4:])
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)


class TestUsbDevice(CommonUsbTestCase):

    maxDiff=None

    def setUp(self):
        endpoints=[EndpointType.BIDIR, EndpointType.IN, EndpointType.BIDIR]

        self.iobuf = TestIoBuf()
        self.dut = UsbDevice(self.iobuf, 0, endpoints)

        self.buffers_in  = {}
        self.buffers_out = {}

        buffer_signals_layout = [
            ('head', 8),
            ('size', 32),
        ]

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)

        self.buffer_signals = []
        for i, ep in enumerate(endpoints):
            if ep & EndpointType.IN:
                buffer_in_signals = Record(buffer_signals_layout, name="ep_%i_in" % i)
                self.buffers_in[i] = []
            else:
                buffer_in_signals = None

            if ep & EndpointType.OUT:
                buffer_out_signals = Record(buffer_signals_layout, name="ep_%i_out" % i)
                self.buffers_out[i] = []
            else:
                buffer_out_signals = None

            self.buffer_signals.append((buffer_in_signals, buffer_out_signals))

    def run_sim(self, stim):
        def padfront():
            yield from self.idle()
            yield from stim()

        run_simulation(self.dut, padfront(), vcd_name="vcd/%s.vcd" % self.id(), clocks={"sys": 4})

    ######################################################################
    ## Helpers
    ######################################################################
    def _update_internal_signals(self):
        """Set the valid/ready/data signals for each endpoint buffer."""
        for i, (in_signals, out_signals) in enumerate(self.buffer_signals):
            if in_signals:
                # Debugging info
                buffer = self.buffers_in[i]
                if buffer is None:
                    yield in_signals.size.eq(-1)
                else:
                    yield in_signals.size.eq(len(buffer))
                    if len(buffer) > 0:
                        yield in_signals.head.eq(buffer[0])

                # Pull bytes from in buffers to the host
                if not buffer:
                    yield self.dut.endp_ins[i].valid.eq(0)
                else:
                    yield self.dut.endp_ins[i].payload.data.eq(buffer[0])
                    yield self.dut.endp_ins[i].valid.eq(1)

                    ready = yield self.dut.endp_ins[i].ready
                    if ready:
                        buffer.pop(0)

            if out_signals:
                # Debugging info
                buffer = self.buffers_out[i]
                if buffer is None:
                    yield out_signals.size.eq(-1)
                else:
                    yield out_signals.size.eq(len(buffer))
                    if len(buffer) > 0:
                        yield out_signals.head.eq(buffer[-1])

                # Set the ready signal
                if buffer is None:
                    yield self.dut.endp_outs[i].ready.eq(0)
                else:
                    yield self.dut.endp_outs[i].ready.eq(1)

                    # Push bytes received from host into out buffers
                    valid = yield self.dut.endp_outs[i].valid
                    if valid:
                        data = yield self.dut.endp_outs[i].data
                        buffer.append(data)

    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        self.ep_print(epaddr, "Set %i: %r", data)
        self.buffers_in[epaddr].extend(data)
        if False:
            yield

    def expect_data(self, ep, data):
        """Expect that an endpoints buffer has given contents."""
        assert ep in self.buffers_out, self.buffers_out.keys()
        assert self.buffers_out[ep] is not None, "Endpoint currently blocked!"
        self.ep_print(epaddr, "Got: %r (expected: %r)", self.buffers_out[ep], data)
        self.assertSequenceEqual(self.buffers_out[ep], data)
        self.buffers_out[ep].clear()
        if False:
            yield


class TestUsbDeviceCpuInterface(CommonUsbTestCase):

    maxDiff=None

    def setUp(self):
        self.endpoints=[EndpointType.BIDIR, EndpointType.IN, EndpointType.BIDIR]

        self.iobuf = TestIoBuf()
        self.dut = UsbDeviceCpuInterface(self.iobuf, self.endpoints)

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)


    def run_sim(self, stim):
        def padfront():
            yield
            yield
            yield
            yield
            yield
            yield
            # Make sure that the endpoints are currently blocked
            ostatus = yield self.dut.ep_0_out.ev.packet.pending
            self.assertTrue(ostatus)
            istatus = yield self.dut.ep_0_in.ev.packet.pending
            self.assertTrue(istatus)
            yield
            yield from self.dut.pullup._out.write(1)
            yield
            # Make sure that the endpoints are currently blocked
            ostatus = yield self.dut.ep_0_out.ev.packet.pending
            self.assertTrue(ostatus)
            istatus = yield self.dut.ep_0_in.ev.packet.pending
            self.assertTrue(istatus)

            yield
            yield from self.idle()
            yield from stim()

        run_simulation(self.dut, padfront(), vcd_name="vcd/%s.vcd" % self.id(), clocks={"usb_48": 4, "sys": 4})

    def _update_internal_signals(self):
        if False:
            yield

    def expect_last_tok(self, epaddr, value):
        endpoint = self.get_endpoint(epaddr)
        last_tok = yield from endpoint.last_tok.read()
        self.assertEqual(last_tok, value)

    ######################################################################
    ## Helpers
    ######################################################################
    def get_endpoint(self, epaddr):
        epdir = EndpointType.epdir(epaddr)
        epnum = EndpointType.epnum(epaddr)
        if epdir == EndpointType.OUT:
            return getattr(self.dut, "ep_%s_out" % epnum)
        elif epdir == EndpointType.IN:
            return getattr(self.dut, "ep_%s_in" % epnum)
        else:
            raise SystemError("Unknown endpoint type: %r" % epdir)

    def pending(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield from endpoint.ev.pending.read()
        return bool(status & 0x2)

    def dtb(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield from endpoint.dtb.read()
        return bool(status)

    def trigger(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield endpoint.ev.packet.trigger
        return bool(status)

    def response(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        response = yield endpoint.response
        return response

    def set_response(self, epaddr, v):
        endpoint = self.get_endpoint(epaddr)
        assert isinstance(v, EndpointResponse), v
        yield from endpoint.respond.write(v)

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        while True:
            trigger = (yield from self.trigger(epaddr))
            if not trigger:
                break
            yield
        # Check the pending flag is raised
        self.assertTrue((yield from self.pending(epaddr)))
        # Clear pending flag
        endpoint = self.get_endpoint(epaddr)
        yield from endpoint.ev.pending.write(0xf)
        yield
        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        self.ep_print(epaddr, "Set: %r", data)

        endpoint = self.get_endpoint(epaddr)

        # Make sure the endpoint is empty
        empty = yield from endpoint.ibuf_empty.read()
        self.assertTrue(empty)

        # If we are writing multiple bytes of data, need to make sure we are
        # not going to ACK the packet until the data is ready.
        if len(data) > 1:
            response = yield endpoint.response
            self.assertNotEqual(response, EndpointResponse.ACK)

        for v in data:
            yield from endpoint.ibuf_head.write(v)
            yield

        yield
        yield
        yield
        if len(data) > 0:
            empty = yield from endpoint.ibuf_empty.read()
            self.assertFalse(bool(empty))
        else:
            empty = yield from endpoint.ibuf_empty.read()
            self.assertTrue(bool(empty))

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        endpoint = self.get_endpoint(epaddr)

        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        actual_data = []
        while range(0, 1024):
            yield from endpoint.obuf_head.write(0)
            empty = yield from endpoint.obuf_empty.read()
            if empty:
                break

            v = yield from endpoint.obuf_head.read()
            actual_data.append(v)
            yield

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)


def _get_bit(epaddr, v):
    """
    >>> _get_bit(0, 0b11)
    True
    >>> _get_bit(0, 0b10)
    False
    >>> _get_bit(0, 0b101)
    True
    >>> _get_bit(1, 0b101)
    False
    """
    return bool(1 << epaddr & v)


def _set_bit(current, epaddr, v):
    """
    >>> bin(_set_bit(0, 0, 1))
    '0b1'
    >>> bin(_set_bit(0, 2, 1))
    '0b100'
    >>> bin(_set_bit(0b1000, 2, 1))
    '0b1100'
    >>> bin(_set_bit(0b1100, 2, 0))
    '0b1000'
    >>> bin(_set_bit(0b1101, 2, 0))
    '0b1001'
    """
    if v:
        return current | 1 << epaddr
    else:
        return current & ~(1 << epaddr)



class TestUsbDeviceCpuMemInterface(CommonUsbTestCase):

    maxDiff=None

    def setUp(self):
        self.iobuf = TestIoBuf()
        self.dut = UsbDeviceCpuMemInterface(self.iobuf, 3)

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)


    def run_sim(self, stim):
        def padfront():
            yield
            yield
            yield
            yield
            yield from self.dut.pullup._out.write(1)
            yield
            yield
            yield
            yield
            yield from self.idle()
            yield from stim()

        run_simulation(self.dut, padfront(), vcd_name="vcd/%s.vcd" % self.id(), clocks={"usb_48": 4, "sys": 4})

    def _update_internal_signals(self):
        if False:
            yield

    ######################################################################
    ## Helpers
    ######################################################################

    def get_module(self, epaddr, name, obj=None):
        if obj is None:
            obj = self.dut
        epdir = EndpointType.epdir(epaddr)
        if epdir == EndpointType.OUT:
            module = getattr(obj, 'o{}'.format(name))
        elif epdir == EndpointType.IN:
            module = getattr(obj, 'i{}'.format(name))
        else:
            raise SystemError("Unknown endpoint type: %r" % epdir)
        return module

    def get_evsrc(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "ep{}".format(epnum), obj=self.dut.packet)

    def get_ptr_csr(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "ptr_ep{}".format(epnum))

    def get_len_csr(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "len_ep{}".format(epnum))

    def set_csr(self, csr, epaddr, v):
        c = yield from csr.read()
        v = _set_bit(c, epaddr, v)
        yield from csr.write(v)

    # Data Toggle Bit
    def dtb(self, epaddr):
        v = yield from self.dut.dtb.read()
        return _get_bit(epaddr, v)

    def set_dtb(self, epaddr):
        yield from self.set_csr(self.dut.dtb, epaddr, 1)

    def clear_dtb(self, epaddr):
        yield from self.set_csr(self.dut.dtb, epaddr, 0)

    # Arm endpoint Bit
    def arm(self, epaddr):
        v = yield from self.dut.arm.read()
        return _get_bit(epaddr, v)

    def set_arm(self, epaddr):
        yield from self.set_csr(self.dut.arm, epaddr, 1)

    def clear_arm(self, epaddr):
        yield from self.set_csr(self.dut.arm, epaddr, 0)

    # Stall endpoint Bit
    def sta(self, epaddr):
        v = yield from self.dut.sta.read()
        return _get_bit(epaddr, v)

    def set_sta(self, epaddr):
        yield from self.set_csr(self.dut.sta, epaddr, 1)

    def clear_sta(self, epaddr):
        yield from self.set_csr(self.dut.sta, epaddr, 0)

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        evsrc = self.get_evsrc(epaddr)
        v = yield evsrc.trigger
        return v

    def pending(self, epaddr):
        evsrc = self.get_evsrc(epaddr)
        v = yield evsrc.pending
        return v

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        while True:
            trigger = (yield from self.trigger(epaddr))
            if not trigger:
                break
            yield

        # Check the pending flag is raised
        self.assertTrue((yield from self.pending(epaddr)))

        # Clear pending flag
        mask = 1 << epaddr
        yield from self.dut.packet.pending.write(mask)
        yield
        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    # Endpoint state -----------------------
    def response(self, epaddr):
        if (yield from self.sta(epaddr)):
            return EndpointResponse.STALL

        pending = yield from self.pending(epaddr)
        armed = yield from self.arm(epaddr)
        if armed and not pending:
            return EndpointResponse.ACK

        return EndpointResponse.NAK

    def set_response(self, epaddr, v):
        assert isinstance(v, EndpointResponse), v
        if v == EndpointResponse.STALL:
            yield from self.set_sta(epaddr)
        else:
            yield from self.clear_sta(epaddr)

        if v == EndpointResponse.ACK:
            yield from self.set_arm(epaddr)
        elif v == EndpointResponse.NAK:
            yield from self.clear_arm(epaddr)

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        self.ep_print(epaddr, "Set: %r", data)

        ep_ptr = yield from self.get_ptr_csr(epaddr).read()
        buf = self.get_module(epaddr, "buf")

        for i, v in enumerate(data):
            yield buf[ep_ptr+i].eq(v)

        ep_len = self.get_len_csr(epaddr)
        yield from ep_len.write(ep_ptr + len(data))

        yield

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        ep_ptr = yield from self.get_ptr_csr(epaddr).read()
        buf = self.get_module(epaddr, "buf")

        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        actual_data = []
        for i in range(len(data), 0, -1):
            d = yield buf[ep_ptr-i]
            actual_data.append(d)

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)


class TestUsbDeviceSimple(CommonUsbTestCase):

    maxDiff=None

    def setUp(self):
        self.iobuf = TestIoBuf()
        self.dut = UsbSimpleFifo(self.iobuf)

        self.states = [
            "WAIT",
            "RECV_DATA",
            "SEND_HAND",
            "SEND_DATA",
            "RECV_HAND",
        ]
        self.decoding = dict(enumerate(self.states))
        self.dut.state = Signal(max=len(self.states))
        self.dut.state._enumeration = self.decoding

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)

        class Endpoint:
            def __init__(self):
                self._response = EndpointResponse.NAK
                self.trigger = False
                self.pending = True
                self.data = None
                self.dtb = True

            def update(self):
                if self.trigger:
                    self.pending = True

            @property
            def response(self):
                if self._response == EndpointResponse.ACK and (self.pending or self.trigger):
                    return EndpointResponse.NAK
                else:
                    return self._response

            @response.setter
            def response(self, v):
                assert isinstance(v, EndpointResponse), repr(v)
                self._response = v

            def __str__(self):
                data = self.data
                if data is None:
                    data = []
                return "<Endpoint p:(%s,%s) %s d:%s>" % (int(self.trigger), int(self.pending), int(self.dtb), len(data))

        self.endpoints = {
            EndpointType.epaddr(0, EndpointType.OUT): Endpoint(),
            EndpointType.epaddr(0, EndpointType.IN):  Endpoint(),
            EndpointType.epaddr(1, EndpointType.OUT): Endpoint(),
            EndpointType.epaddr(1, EndpointType.IN):  Endpoint(),
            EndpointType.epaddr(2, EndpointType.OUT): Endpoint(),
            EndpointType.epaddr(2, EndpointType.IN):  Endpoint(),
        }

    def run_sim(self, stim):
        def padfront():
            yield from self.next_state("WAIT")
            yield
            yield
            yield
            yield
            yield from self.dut.pullup._out.write(1)
            yield
            yield
            yield
            yield
            yield from self.idle()
            yield from stim()

        print()
        print("-"*10)
        run_simulation(self.dut, padfront(), vcd_name="vcd/%s.vcd" % self.id(), clocks={"usb_48": 4, "sys": 4})
        print("-"*10)

    def recv_packet(self):
        rx = (yield from self.dut.ev.pending.read()) & 0b1
        if not rx:
            return

        actual_data = []
        while range(0, 1024):
            yield from self.dut.obuf_head.write(0)
            empty = yield from self.dut.obuf_empty.read()
            if empty:
                break

            v = yield from self.dut.obuf_head.read()
            actual_data.append(v)
            yield

        yield from self.dut.ev.pending.write(0b1)
        yield

        #self.assertEqual(actual_data[0], 0b00000001)
        return actual_data

    def send_packet(self, pid, data=None):
        yield from self.dut.arm.write(0)
        armed = yield from self.dut.arm.read()
        self.assertFalse(armed)

        empty = yield from self.dut.obuf_empty.read()
        self.assertTrue(empty)

        #           sync,       pid
        pkt_data = [0b10000000, pid | ((0b1111 ^ pid) << 4)]
        if data is None:
            assert pid in (PID.ACK, PID.NAK, PID.STALL), (pid, data)
        else:
            assert pid in (PID.DATA0, PID.DATA1), pid
            pkt_data += data
            pkt_data += crc16(data)

        print("send_packet", pid, data)
        print("send_packet", pkt_data)
        for d in pkt_data:
            yield from self.dut.ibuf_head.write(d)
            yield
            yield
            yield
            yield

        empty = yield from self.dut.ibuf_empty.read()
        self.assertFalse(empty)

        yield from self.dut.arm.write(1)

    def next_state(self, state):
        self.assertIn(state, self.states)
        yield self.dut.state.eq(self.states.index(state))
        self.state = state

    def _update_internal_signals(self):
        def decode_pid(pkt_data):
            pkt_data = encode_data(pkt_data[:1])
            pidt = int(pkt_data[0:4][::-1], 2)
            pidb = int(pkt_data[4:8][::-1], 2)
            #self.assertEqual(pidt ^ 0b1111, pidb)
            return PID(pidt)

        for ep in self.endpoints.values():
            if ep.trigger:
                ep.pending = True
                ep.trigger = False
        del ep

        if self.state == "WAIT":
            self.ep = None
            self.handshake = None

            pkt_data = yield from self.recv_packet()
            if not pkt_data:
                return
            print(pkt_data)
            self.assertEqual(len(pkt_data), 3, pkt_data)
            pid = decode_pid(pkt_data)
            pkt_data = encode_data(pkt_data)
            addr = int(pkt_data[8:8+7][::-1], 2)
            endp = int(pkt_data[8+7:8+7+4][::-1], 2)
            crc5 = int(pkt_data[8+7+4:][::-1], 2)

            print("WAIT      pid:", pid, "addr:", addr, "ep:", endp, "crc5:", crc5)
            self.assertEqual(crc5, crc5_token(addr, endp))

            if pid == PID.SETUP or pid == PID.OUT:
                self.ep = self.endpoints[EndpointType.epaddr(endp, EndpointType.OUT)]
                if pid == PID.SETUP:
                    self.handshake = EndpointResponse.ACK
                    self.ep.response = EndpointResponse.NAK
                    self.ep.dtb = False

                    iep = self.endpoints[EndpointType.epaddr(endp, EndpointType.IN)]
                    self.assertIsNot(self.ep, iep)
                    iep.response = EndpointResponse.NAK
                    iep.dtb = True
                    print(self.ep, iep)
                else:
                    self.handshake = self.ep.response
                yield from self.next_state("RECV_DATA")

            elif pid == PID.IN:
                self.ep = self.endpoints[EndpointType.epaddr(endp, EndpointType.IN)]
                self.handshake = self.ep.response

                if self.ep.response == EndpointResponse.ACK:
                    #self.assertIsNotNone(self.ep.data)
                    if self.ep.data is None:
                        self.ep.data = []
                    yield from self.next_state("SEND_DATA")
                else:
                    yield from self.next_state("SEND_HAND")
            else:
                assert False, pid

        elif self.state == "RECV_DATA":
            self.assertIsNotNone(self.ep)
            pkt_data = yield from self.recv_packet()
            if not pkt_data:
                return

            pid = decode_pid(pkt_data)
            print("RECV_DATA pid:", pid, "data:", pkt_data)

            if self.handshake == EndpointResponse.ACK:
                self.assertIsNone(self.ep.data)
                self.assertIn(encode_pid(pid), (encode_pid(PID.DATA0), encode_pid(PID.DATA1)))
                self.assertSequenceEqual(pkt_data[-2:], crc16(pkt_data[1:-2]))
                self.ep.data = pkt_data[1:-2]

            yield from self.next_state("SEND_HAND")

        elif self.state == "SEND_HAND":
            self.assertIsNotNone(self.ep)
            self.assertIsNotNone(self.handshake)
            pid = {
                EndpointResponse.STALL: PID.STALL,
                EndpointResponse.NAK:   PID.NAK,
                EndpointResponse.ACK:   PID.ACK,
            }[self.handshake]
            print("SEND_HAND pid:", pid)
            yield from self.send_packet(pid)
            if self.handshake == EndpointResponse.ACK:
                self.ep.trigger = True
                self.ep.dtb = not self.ep.dtb
            yield from self.next_state("WAIT")

        elif self.state == "SEND_DATA":
            self.assertIsNotNone(self.ep)
            self.assertIsNotNone(self.ep.data)
            pid = [PID.DATA0, PID.DATA1][self.ep.dtb]
            print("SEND_DATA pid:", pid, "data:", self.ep.data)
            yield from self.send_packet(pid, self.ep.data)
            self.ep.data = None
            yield from self.next_state("RECV_HAND")

        elif self.state == "RECV_HAND":
            self.assertIsNotNone(self.ep)
            pkt_data = yield from self.recv_packet()
            if not pkt_data:
                return

            pid = decode_pid(pkt_data)
            print("RECV_HAND pid:", pid)
            if pid != PID.ACK:
                raise SystemError(pkt_data)

            self.ep.trigger = True
            self.ep.dtb = not self.ep.dtb

            yield from self.next_state("WAIT")

    ######################################################################
    ## Helpers
    ######################################################################

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        yield from self._update_internal_signals()
        return self.endpoints[epaddr].trigger

    def pending(self, epaddr):
        yield from self._update_internal_signals()
        return self.endpoints[epaddr].pending

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        for i in range(0, 100):
            trigger = (yield from self.trigger(epaddr))
            if not trigger:
                break
            yield
        self.assertFalse(trigger)

        # Check the pending flag is raised
        self.assertTrue((yield from self.pending(epaddr)))

        # Clear pending flag
        self.endpoints[epaddr].pending = False
        self.ep_print(epaddr, "clear_pending")

        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    # Endpoint state -----------------------
    def response(self, epaddr):
        if False:
            yield
        return self.endpoints[epaddr].response

    def set_response(self, epaddr, v):
        assert isinstance(v, EndpointResponse), v
        if False:
            yield
        self.ep_print(epaddr, "set_response: %s", v)
        self.endpoints[epaddr].response = v

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        if False:
            yield

        self.ep_print(epaddr, "Set: %r", data)
        self.endpoints[epaddr].data = data

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        self.ep_print(epaddr, "expect_data: %s", data)
        actual_data = self.endpoints[epaddr].data
        assert actual_data is not None
        self.endpoints[epaddr].data = None

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)

    def dtb(self, epaddr):
        if False:
            yield
        print("dtb", epaddr, self.endpoints[epaddr])
        return self.endpoints[epaddr].dtb




if __name__ == '__main__':
    import doctest
    doctest.testmod()
    unittest.main()
