#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fifo
from migen.genlib import cdc

from litex.soc.interconnect import stream
from litex.soc.interconnect import wishbone
from litex.soc.interconnect import csr_eventmanager as ev
from litex.soc.interconnect.csr import *

from litex.soc.cores.gpio import GPIOOut


class RxPacketDetect(Module):
    """Packet Detection

    Full Speed packets begin with the following sequence:

        KJKJKJKK

    This raw sequence corresponds to the following data:

        00000001

    The bus idle condition is signaled with the J state:

        JJJJJJJJ

    This translates to a series of '1's since there are no transitions.  Given
    this information, it is easy to detect the beginning of a packet by looking
    for 00000001.

    The end of a packet is even easier to detect.  The end of a packet is
    signaled with two SE0 and one J.  We can just look for the first SE0 to
    detect the end of the packet.

    Packet detection can occur in parallel with bitstuff removal.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.10

    Input Ports
    ------------
    Input ports are passed in via the constructor.

    i_valid : Signal(1)
        Qualifier for all of the output signals. Indicates one bit of valid
        data is present on the outputs.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    i_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_pkt_start : Signal(1)
        Asserted for one clock on the last bit of the sync.

    o_pkt_active : Signal(1)
        Asserted while in the middle of a packet.

    o_pkt_end : Signal(1)
        Asserted for one clock after the packet ends.
    """

    def __init__(self, i_valid, i_data, i_se0):
        self.submodules.pkt = pkt = FSM()

        pkt_start = Signal()
        pkt_active = Signal()
        pkt_end = Signal()

        for i in range(5):
            pkt.act("D%d" % i,
                If(i_valid,
                    If(i_data | i_se0,
                        # Receiving '1' or SE0 early resets the packet start counter.
                        NextState("D0")
                    ).Else(
                        # Receiving '0' increments the packet start counter.
                        NextState("D%d" % (i + 1))
                    )
                )
            )

        pkt.act("D5",
            If(i_valid,
                # once we get a '1', the packet is active
                If(i_data,
                    pkt_start.eq(1),
                    pkt_active.eq(1),
                    NextState("PKT_ACTIVE")
                )
            )
        )

        pkt.act("PKT_ACTIVE",
            pkt_active.eq(1),
            If(i_valid,
                # once we get an SE0, the packet is over
                If(i_se0,
                    pkt_end.eq(1),
                    pkt_active.eq(0),
                    NextState("D0")
                )
            )
        )

        # pass all of the outputs through a pipe stage
        self.o_pkt_start = Signal(1)
        self.o_pkt_active = Signal(1)
        self.o_pkt_end = Signal(1)

        self.sync += [
            self.o_pkt_start.eq(pkt_start),
            self.o_pkt_active.eq(pkt_active),
            self.o_pkt_end.eq(pkt_end),
        ]


class RxShifter(Module):
    """RxShifter

    A shifter is responsible for shifting in serial bits and presenting them
    as parallel data.  The shifter knows how many bits to shift and has
    controls for resetting the shifter.

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Number of bits to shift in.

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_data : Signal(1)
        Serial input data. Qualified by i_valid.

    i_reset : Signal(1)
        Reset the shift register and start shifting in new data when ready.
        This is not a normal migen reset, this reset must be asserted by the
        control logic using this module.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_output : Signal(width)
        Shifted in data.  Qualified by o_valid.

    o_put : Signal(1)
        Asserted for one clock once the register is full.
    """
    def __init__(self, width, i_data, i_reset):

        self.i_data = Signal()
        self.i_reset = Signal()

        self.o_data = Signal(width)
        self.o_put = Signal()

        # Instead of using a counter, we will use a sentinel bit in the shift
        # register to indicate when it is full.
        shift_reg = Signal(width + 1)

        # shift valid incoming data in while not full
        self.sync += [
            If(i_reset,
                shift_reg.eq(1 << width),
            ).Else(
                shift_reg.eq(Cat(shift_reg[1:width + 1], i_data))
            ),
            self.o_put.eq(shift_reg[0:2] == 0b10)
        ]

        self.comb += [
            self.o_output.eq(shift_reg[1:width + 1])
        ]



class RxCrcChecker(Module):
    """CRC Checker

    Checks the CRC of a serial stream of data.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 8.3.5
    https://en.wikipedia.org/wiki/Cyclic_redundancy_check

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the CRC.

    polynomial : int
        CRC polynomial in integer form.

    initial : int
        Initial value of the CRC register before data starts shifting in.

    residual : int
        Value of the CRC register if all the shifted in data is valid.

    Input Ports
    ------------
    Input ports are passed in via the constructor.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    i_reset : Signal(1)
        Resets the CRC calculation back to the initial state.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_crc_good : Signal()
        CRC value is good.
    """
    def __init__(self, width, polynomial, initial, residual):
        self.i_data = Signal()
        self.i_reset = Signal()

        crc = Signal(width)
        crc_good = Signal(1)
        crc_invert = Signal(1)

        self.comb += [
            crc_good.eq(crc == residual),
            crc_invert.eq(i_data ^ crc[width - 1])
        ]

        for i in range(width):
            rhs = None
            if i == 0:
                rhs = crc_invert
            else:
                if (polynomial >> i) & 1:
                    rhs = crc[i - 1] ^ crc_invert
                else:
                    rhs = crc[i - 1]

            self.sync += [
                If(i_reset,
                    crc[i].eq((initial >> i) & 1)
                ).Elif(i_valid,
                    crc[i].eq(rhs)
                )
            ]

        # flop all outputs
        self.o_crc_good = Signal(1)

        self.sync += [
            self.o_crc_good.eq(crc_good)
        ]


class RxPacketClocks(Module):
    def __init__(self, usbp_raw, usbn_raw):

        # 48MHz clock domain
        self.submodules.clock_data_recovery = clock_data_recovery = ClockDomainsRenamer("usb_48")(
            RxClockDataRecovery(usbp_raw, usbn_raw),
        )

        self.o_bit_strobe = clock_data_recovery.line_state_valid

        self.raw_valid = clock_data_recovery.line_state_valid
        self.raw_dj = clock_data_recovery.line_state_dj
        self.raw_dk = clock_data_recovery.line_state_dk
        self.raw_se0 = clock_data_recovery.line_state_se0

        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(
            RxNRZIDecoder(
                i_valid = clock_data_recovery.line_state_valid,
                i_dj = clock_data_recovery.line_state_dj,
                i_dk = clock_data_recovery.line_state_dk,
                i_se0 = clock_data_recovery.line_state_se0,
            ),
        )

        bit_data = Signal()
        self.specials += cdc.MultiReg(nrzi.o_data, bit_data, n=2)

        # 12MHz clock domain
        self.submodules.bitstuff = bitstuff = ClockDomainsRenamer("usb_12")(
            RxBitstuffRemover(
                i_valid = nrzi.o_valid & pkt_det.o_pkt_active,
                i_data = bit_data,
                i_se0 = nrzi.o_se0,
            ),
        )

        reset = Signal()
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(
            RxShifter(
                8,
                i_valid = bitstuff.o_valid,
                i_data = bitstuff.o_data,
                i_reset = reset,
            ),
        )
        self.comb += [
            reset.eq(shifter.o_full | pkt_det.o_pkt_start),
        ]
        pass


class RxPacketSimple(Module):
    def __init__(self, usbp_raw, usbn_raw):
        self.submodules.clock_data_recovery = clock_data_recovery = RxClockDataRecovery(
            usbp_raw,
            usbn_raw,
        )
        self.o_bit_strobe = clock_data_recovery.line_state_valid

        self.raw_valid = clock_data_recovery.line_state_valid
        self.raw_dj = clock_data_recovery.line_state_dj
        self.raw_dk = clock_data_recovery.line_state_dk
        self.raw_se0 = clock_data_recovery.line_state_se0

        self.submodules.nrzi = nrzi = RxNRZIDecoder(
            i_valid = clock_data_recovery.line_state_valid,
            i_dj = clock_data_recovery.line_state_dj,
            i_dk = clock_data_recovery.line_state_dk,
            i_se0 = clock_data_recovery.line_state_se0,
        )

        self.submodules.pkt_det = pkt_det = RxPacketDetect(
            i_valid = nrzi.o_valid,
            i_data = nrzi.o_data,
            i_se0 = nrzi.o_se0,
        )

        self.submodules.bitstuff = bitstuff = RxBitstuffRemover(
            i_valid = nrzi.o_valid & pkt_det.o_pkt_active,
            i_data = nrzi.o_data,
            i_se0 = nrzi.o_se0,
        )

        reset = Signal()
        self.submodules.shifter = shifter = RxShifter(
            8,
            i_valid = bitstuff.o_valid,
            i_data = bitstuff.o_data,
            i_reset = reset,
        )
        self.comb += [
            reset.eq(shifter.o_full | pkt_det.o_pkt_start),
        ]

        self.o_data_put = Signal()
        self.o_data_payload = Signal(8)
        self.sync += [
            If(shifter.o_put,
                self.o_data_payload.eq(shifter.o_output),
            ),
            self.o_data_put.eq(shifter.o_put & pkt_det.o_pkt_active),
        ]

        self.o_pkt_end = Signal()
        self.comb += [
            self.o_pkt_end.eq(pkt_det.o_pkt_end),
        ]


class RxPacketDecode(Module):
    """
    Packet Decode

    Packet decode is responsible for extracting packet fields and emitting
    control signals that indicate which portion of the packet is currently
    being received.

    Packet decode must occur after bitstuff removal.

    https://www.pjrc.com/teensy/beta/usb20.pdf

    Input Ports
    ------------
    Input ports are passed in via the constructor.

    i_valid : Signal(1)
        Qualifier for input data and se0 signals. Indicates one bit of valid
        data is present on those inputs.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    i_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.

    i_bitstuff_error : Signal(1)
        Indicates a bitstuff error has been detected.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_pkt_start: Signal(1)
        Asserted for one clock to signal the start of a packet.

    o_pkt_pid : Signal(4)
        Packet PID. Qualified with o_pkt_pid_good.

    o_pkt_token_payload : Signal(11)
        Token packet payload.

    o_pkt_data : Signal(8)
        From data packet payload. Qualified by o_pkt_data_put.

    o_pkt_data_put : Signal(1)
        Asserted for one clock to indicate o_pkt_data is valid.

    o_pkt_good : Signal(1)
        Indicates the packet has passed all relevant consistency checks for
        PID, CRC5, CRC16, and Bitstuff Errors.

    o_pkt_end: Signal(1)
        Asserted for one clock to signal the end of a packet.

    """

    def __init__(self, i_valid, i_data, i_se0, i_bitstuff_error):
        #######################################################################
        #
        # align incoming data such that pkt_start is asserted the last clock
        # of the sync. this ensures that all the internal state can be reset
        # before it needs to begin processing a new packet.
        #
        valid = Signal()
        data = Signal()
        se0 = Signal()
        bitstuff_error = Signal()

        self.sync += [
            valid.eq(i_valid),
            data.eq(i_data),
            se0.eq(i_se0),
            bitstuff_error.eq(i_bitstuff_error)
        ]

        self.submodules.pkt_det = pkt_det = RxPacketDetect(
            i_valid,
            i_data,
            i_se0
        )

        pkt_start = pkt_det.o_pkt_start
        pkt_active = Signal()
        self.pkt_end = pkt_end = Signal()

        self.sync += [
            pkt_active.eq(pkt_det.o_pkt_active),
            pkt_end.eq(pkt_det.o_pkt_end)
        ]

        i_reset = Signal()
        self.submodules.shifter = RxShifter(8, i_valid, i_data, i_reset)
        shifter = self.shifter

        # PID
        self.start_tok = Signal()
        self.end_pid = Signal()

        # No start handshake
        self.end_handshake = Signal()

        # Token packet
        self.start_token = Signal()
        self.end_token = Signal()

        # Data packet
        self.start_data = Signal()
        self.put_data = Signal()
        self.end_data = Signal()

        # Incoming data pipeline
        self.data_n0 = Signal(8)
        self.data_n1 = Signal(8)
        self.sync += [
            If(shifter.o_put,
                self.data_n1.eq(self.data_n0),
                self.data_n0.eq(shifter.o_output),
            ),
        ]
        self.comb += [
            i_reset.eq(shifter.o_put),
        ]

        self.submodules.state = state = FSM()

        state.act("WAIT_SYNC",
            If(pkt_det.o_pkt_start,
                i_reset.eq(1),
                self.start_tok.eq(1),
                NextState("WAIT_PID"),
            ),
        )

        state.act("WAIT_PID",
            If(shifter.o_put,
                self.end_pid.eq(1),

                # Handshake
                If(shifter.o_output[0:2] == 0b10,
                    self.end_handshake.eq(1),
                    NextState("WAIT_SYNC"),

                # Token
                ).Elif(shifter.o_output[0:2] == 0b01,
                    self.start_token.eq(1),
                    NextState("WAIT_TOK0"),

                # Data
                ).Elif(shifter.o_output[0:2] == 0b11,
                    self.start_data.eq(1),
                    NextState("WAIT_DAT0"),
                ),
            ),
        )

        # Capture the PID
        self.o_pid = Signal(4)
        self.sync += [
            If(self.end_pid,
                self.o_pid.eq(shifter.o_output[0:4]),
            ),
        ]

        # Wait for first byte of TOKEN data
        state.act("WAIT_TOK0",
            If(shifter.o_put,
                #NextValue(self.o_addr, shifter.o_output[0:6]),
                #NextValue(self.o_ep[0], shifter.o_output[0]),
                NextState("WAIT_TOK1"),
            )
        )
        # Wait for second byte of TOKEN data
        state.act("WAIT_TOK1",
            If(shifter.o_put,
                #NextValue(self.o_ep[1:3], shifter.o_output[0:2]),
                self.end_token.eq(1),
                NextState("WAIT_SYNC"),
            ),
        )

        # Capture the address and endpoint
        self.o_addr = Signal(7)
        self.o_ep   = Signal(4)
        self.sync += [
            If(self.end_token,
                self.o_addr.eq(self.data_n0[0:6]),
                self.o_ep.eq(Cat(self.data_n0[7], shifter.o_output[0:2])),
            ),
        ]

        # Wait two bytes
        state.act("WAIT_DAT0",
            If(shifter.o_put, NextState("WAIT_DAT1")),
        )
        state.act("WAIT_DAT1",
            If(shifter.o_put, NextState("WAIT_DATX")),
        )
        state.act("WAIT_DATX",
            self.put_data.eq(shifter.o_put),
            If(pkt_det.o_pkt_end, NextState("WAIT_SYNC"),
                self.end_data.eq(1),
            ),
        )


class UsbFsRx(Module):
    """
    Input Ports
    -----------
    Input ports are passed in via the constructor.

    usbp_raw : Signal(1)
        Raw USB+ input from the FPGA IOs, no need to synchronize.

    usbn_raw : Signal(1)
        Raw USB- input from the FPGA IOs, no need to synchronize.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_bit_strobe : Signal(1)
        Asserted for one clock in the middle of each USB bit.

    o_pkt_start : Signal(1)
        Asserted for one clock to signal the start of a packet.

    o_pkt_pid : Signal(4)
        Packet PID. Qualified with o_pkt_pid_good.

    o_pkt_token_payload : Signal(11)
        Token packet payload.

    o_pkt_data : Signal(8)
        From data packet payload. Qualified by o_pkt_data_put.

    o_pkt_data_put : Signal(1)
        Asserted for one clock to indicate o_pkt_data is valid.

    o_pkt_good : Signal(1)
        Indicates the packet has passed all relevant consistency checks for
        PID, CRC5, CRC16, and Bitstuff Errors.

    o_pkt_end: Signal(1)
        Asserted for one clock to signal the end of a packet.
    """
    def __init__(self, usbp_raw, usbn_raw):
        self.submodules.clock_data_recovery = clock_data_recovery = RxClockDataRecovery(
            usbp_raw,
            usbn_raw
        )

        self.raw_valid = clock_data_recovery.line_state_valid
        self.raw_dj = clock_data_recovery.line_state_dj
        self.raw_dk = clock_data_recovery.line_state_dk
        self.raw_se0 = clock_data_recovery.line_state_se0

        self.submodules.nrzi = nrzi = RxNRZIDecoder(
            i_valid = clock_data_recovery.line_state_valid,
            i_dj = clock_data_recovery.line_state_dj,
            i_dk = clock_data_recovery.line_state_dk,
            i_se0 = clock_data_recovery.line_state_se0
        )

        self.submodules.bitstuff = bitstuff = RxBitstuffRemover(
            i_valid = nrzi.o_valid,
            i_data = nrzi.o_data,
            i_se0 = nrzi.o_se0
        )

        self.submodules.decode = decode = RxPacketDecode(
            i_valid = bitstuff.o_valid,
            i_data = bitstuff.o_data,
            i_se0 = bitstuff.o_se0,
            i_bitstuff_error = bitstuff.o_bitstuff_error
        )

        self.o_bit_strobe = clock_data_recovery.line_state_valid
        self.o_pkt_start = decode.start_tok
        self.o_pkt_end = decode.pkt_end


###############################################################################
###############################################################################
###############################################################################
######
###### Physical Layer Transmit Path
######
###############################################################################
###############################################################################
###############################################################################
#
# Notes
# -----
# - The bitstuffer is the only part of the pipeline that can flow-control the
#   tx pipeline. It will generate a "stall" signal to pause the pipeline while
#   a bit is stuffed.
#
# - TxShifters are strung together to sequence the packet encoding.
#
# - Should TxCrcGenerators shift their data out directly or use a TxShifter?
#
# - The transmit pipeline should support tokens so that it can be reused as
#   a simple host.
#
# Structure
# ---------
# - usb_fs_tx : UsbFsTx
#   - sync_shifter : TxShifter
#   - pid_shifter : TxShifter
#   - token_shifter : TxShifter
#   - crc5_generator : TxCrcGenerator
#   - crc5_shifter : TxShifter
#   - data_shifter : TxShifter
#   - crc16_generator : TxCrcGenerator
#   - crc16_shifter : TxShifter
#   - bitstuffer : TxBitstuffer
#   - nrzi_encoder : TxNrziEncoder


class TxShifter(Module):
    """
    Transmit Shifter

    TxShifter accepts parallel data and shifts it out serially.

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the data to be shifted.

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_put : Signal(1)
        Load shifter with data to transmit.

    i_shift : Signal(1)
        One bit of data will be shifted out for each clock this is asserted.

    i_data : Signal(width)
        Data to be transmitted.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_data : Signal(1)
        Serial data output.

    o_empty : Signal(1)
        Asserts when the shifter is empty.

    """
    def __init__(self, width, i_put, i_shift, i_data):
        shifter = Signal(width)
        pos = Signal(width+1, reset=0b1)

        empty = Signal(1)
        self.sync += [
            If(i_put,
                shifter.eq(i_data),
                pos.eq(1 << width),
            ).Elif(i_shift,
                If(~empty,
                    pos.eq(pos >> 1),
                    shifter.eq(shifter >> 1),
                ),
            ),
        ]

        self.o_data = Signal(1)

        self.comb += [
            empty.eq(pos[0]),
            self.o_data.eq(shifter[0]),
        ]

        self.o_empty = Signal(1)
        self.comb += [
            self.o_empty.eq(empty),
        ]



class TxCrcGenerator(Module):
    """
    Transmit CRC Generator

    TxCrcGenerator generates a running CRC.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 8.3.5
    https://en.wikipedia.org/wiki/Cyclic_redundancy_check

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the CRC.

    polynomial : int
        CRC polynomial in integer form.

    initial : int
        Initial value of the CRC register before data starts shifting in.

    Input Ports
    ------------
    Input ports are passed in via the constructor.

    i_reset : Signal(1)
        Resets the CRC calculation back to the initial state.

    i_data : Signal(1)
        Serial data to generate CRC for.
        Qualified by i_shift.

    i_shift : Signal(1)
        Qualifier for input data and se0 signals. Indicates one bit of valid
        data is present on those inputs.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_crc : Signal(width)
        Current CRC value.

    """
    def __init__(self, width, polynomial, initial, i_reset, i_data, i_shift):
        crc = Signal(width)
        crc_invert = Signal(1)

        self.comb += [
            crc_invert.eq(i_data ^ crc[width - 1])
        ]

        for i in range(width):
            rhs_data = None
            if i == 0:
                rhs_data = crc_invert
            else:
                if (polynomial >> i) & 1:
                    rhs_data = crc[i - 1] ^ crc_invert
                else:
                    rhs_data = crc[i - 1]

            self.sync += [
                If(i_reset,
                    crc[i].eq((initial >> i) & 1)
                ).Elif(i_shift,
                    crc[i].eq(rhs_data)
                )
            ]

        self.o_crc = Signal(width)

        for i in range(width):
            self.comb += [
                self.o_crc[i].eq(1 ^ crc[width - i - 1]),
            ]



class TxBitstuffer(Module):
    """
    Bitstuff Insertion

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.

    The TxBitstuffer is the only component in the transmit pipeline that can
    delay transmission of serial data.  It is therefore responsible for
    generating the bit_strobe signal that keeps the pipe moving forward.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

    Input Ports
    ------------
    Input ports are passed in via the constructor.

    i_valid : Signal(1)
        Qualifies oe, data, and se0.

    i_oe : Signal(1)
        Indicates that the transmit pipeline should be driving USB.

    i_data : Signal(1)
        Data bit to be transmitted on USB.

    i_se0 : Signal(1)
        Overrides value of i_data when asserted and indicates that SE0 state
        should be asserted on USB.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.

    o_stall : Signal(1)
        Used to apply backpressure on the tx pipeline.

    o_valid : Signal(1)
        Indicates that the current o_data should be transmitted onto USB.

    o_data : Signal(1)
        Data bit to be transmitted on USB. Qualified by o_valid.

    o_se0 : Signal(1)
        Overrides value of o_data when asserted and indicates that SE0 state
        should be asserted on USB. Qualified by o_valid.

    o_oe : Signal(1)
        Indicates that the transmit pipeline should be driving USB.
    """
    def __init__(self, i_valid, i_oe, i_data, i_se0):
        self.submodules.stuff = stuff = FSM()

        stuff_bit = Signal(1)

        for i in range(6):
            stuff.act("D%d" % i,
                If(i_valid,
                    If(i_data,
                        # Receiving '1' increments the bitstuff counter.
                        NextState("D%d" % (i + 1))
                    ).Else(
                        # Receiving '0' resets the bitstuff counter.
                        NextState("D0")
                    )
                )
            )

        stuff.act("D6",
            # stuff a bit
            stuff_bit.eq(1),

            If(i_valid,
                # Reset the bitstuff counter
                NextState("D0")
            )
        )

        self.o_stall = Signal(1)
        self.o_valid = Signal(1)
        self.o_data = Signal(1)
        self.o_se0 = Signal(1)
        self.o_oe = Signal(1)

        self.comb += [
            self.o_stall.eq(stuff_bit)
        ]

        # flop outputs
        self.sync += [
            If(i_valid,
                self.o_data.eq(i_data & ~stuff_bit),
                self.o_se0.eq(i_se0),
                self.o_oe.eq(i_oe)
            )
        ]



class TxNrziEncoder(Module):
    """
    NRZI Encode

    In order to ensure there are enough bit transitions for a receiver to recover
    the clock usb uses NRZI encoding.  This module processes the incoming
    dj, dk, se0, and valid signals and decodes them to data values.  It
    also pipelines the se0 signal and passes it through unmodified.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.8
    https://en.wikipedia.org/wiki/Non-return-to-zero

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_valid : Signal(1)
        Qualifies oe, data, and se0.

    i_oe : Signal(1)
        Indicates that the transmit pipeline should be driving USB.

    i_data : Signal(1)
        Data bit to be transmitted on USB. Qualified by o_valid.

    i_se0 : Signal(1)
        Overrides value of o_data when asserted and indicates that SE0 state
        should be asserted on USB. Qualified by o_valid.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.

    o_usbp : Signal(1)
        Raw value of USB+ line.

    o_usbn : Signal(1)
        Raw value of USB- line.

    o_oe : Signal(1)
        When asserted it indicates that the tx pipeline should be driving USB.
    """

    def __init__(self, i_valid, i_oe, i_data, i_se0):
        # Simple state machine to perform NRZI encoding.
        self.submodules.nrzi = nrzi = FSM()

        usbp = Signal(1)
        usbn = Signal(1)
        oe = Signal(1)

        # wait for new packet to start
        nrzi.act("IDLE",
            usbp.eq(1),
            usbn.eq(0),
            oe.eq(0),

            If(i_valid,
                If(i_oe,
                    # first bit of sync always forces a transition, we idle
                    # in J so the first output bit is K.
                    NextState("DK")
                )
            )
        )

        # the output line is in state J
        nrzi.act("DJ",
            usbp.eq(1),
            usbn.eq(0),
            oe.eq(1),

            If(i_valid,
                If(i_se0,
                    NextState("SE0")
                ).Elif(i_data,
                    NextState("DJ")
                ).Else(
                    NextState("DK")
                )
            )
        )

        # the output line is in state K
        nrzi.act("DK",
            usbp.eq(0),
            usbn.eq(1),
            oe.eq(1),

            If(i_valid,
                If(i_se0,
                    NextState("SE0")
                ).Elif(i_data,
                    NextState("DK")
                ).Else(
                    NextState("DJ")
                )
            )
        )

        # the output line is in SE0 state
        nrzi.act("SE0",
            usbp.eq(0),
            usbn.eq(0),
            oe.eq(1),

            If(i_valid,
                If(i_se0,
                    NextState("SE0")
                ).Else(
                    NextState("EOPJ")
                )
            )
        )

        # drive the bus back to J before relinquishing control
        nrzi.act("EOPJ",
            usbp.eq(1),
            usbn.eq(0),
            oe.eq(1),

            If(i_valid,
                NextState("IDLE")
            )
        )

        # flop all outputs
        self.o_usbp = Signal(1)
        self.o_usbn = Signal(1)
        self.o_oe = Signal(1)

        self.sync += [
            self.o_oe.eq(oe),
            self.o_usbp.eq(usbp),
            self.o_usbn.eq(usbn),
        ]


class TxPacketSimple(Module):

    def __init__(self, i_bit_strobe):

        self.i_more_data = Signal()
        self.i_data = Signal(8)

        empty = Signal()
        bitstuff_stall = Signal()

        bit_strobe = Signal()
        have_data = Signal()
        self.comb += [
            bit_strobe.eq(i_bit_strobe & ~bitstuff_stall),
            have_data.eq(self.i_more_data | ~empty),
        ]

        self.submodules.shifter = shifter = TxShifter(
            width = 8,
            i_put = self.i_more_data & empty,
            i_shift = bit_strobe, #i_bit_strobe & ~bitstuff_stall,
            i_data = self.i_data,
        )
        self.comb += [
            empty.eq(shifter.o_empty),
        ]

        self.pkt_end = Signal()
        self.pkt_active = Signal()
        self.pkt_start = Signal()
        self.pkt_se0 = Signal()

        self.submodules.pkt = pkt = FSM()
        pkt.act("IDLE",
            If(bit_strobe, #i_bit_strobe & ~bitstuff_stall,
                If(have_data,
                    self.pkt_start.eq(1),
                    self.pkt_active.eq(1),
                    NextState("PKT_ACTIVE"),
                ),
            ),
        )
        pkt.act("PKT_ACTIVE",
            self.pkt_active.eq(1),
            If(bit_strobe, #i_bit_strobe & ~bitstuff_stall,
                If(~have_data,
                    self.pkt_active.eq(0),
                    self.pkt_end.eq(1),
                    self.pkt_se0.eq(1),
                    NextState("PKT_SE0"),
                ),
            ),
        )

        pkt.act("PKT_SE0",
            self.pkt_se0.eq(1),
            If(i_bit_strobe,
                NextState("IDLE"),
            ),
        )

        self.submodules.bitstuffer = bitstuffer = TxBitstuffer(
            i_valid = i_bit_strobe,
            i_oe = have_data,
            i_data = shifter.o_data,
            i_se0 = self.pkt_se0,
        )
        self.comb += [
             bitstuff_stall.eq(bitstuffer.o_stall)
        ]

        self.submodules.nrzi = nrzi = TxNrziEncoder(
            i_valid = i_bit_strobe,
            i_oe = bitstuffer.o_oe,
            i_data = bitstuffer.o_data,
            i_se0 = bitstuffer.o_se0
        )

        ######################################################################
        #
        # Flop all outputs
        #
        self.o_usbp = Signal(1)
        self.o_usbn = Signal(1)
        self.o_oe = Signal(1)

        self.sync += [
            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe)
        ]


class UsbFsTx(Module):
    """
    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_bit_strobe : Signal(1)
        Asserted one clock out of every four.

    i_pkt_start : Signal(1)
        Asserted for one clock to begin transmitting the packet.

    i_pid : Signal(4)
        PID of packet to send.  Qualified by i_pkt_start.

    i_token_payload : Signal(11)
        Token payload to send for IN, OUT, SETUP, and SOF packets. This is
        only needed for hosts and not devices.  Qualified by i_pkt_start.

    i_data_valid : Signal(1)
        Asserted while i_data_payload contains valid data to transmit.

    i_data_payload : Signal(8)
        Data to transmit for a data packet. Qualified by i_data_valid.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.

    o_data_get : Signal(1)
        Asserted for one clock to indicate the data present on i_data_payload
        has been consumed.

    o_pkt_end : Signal(1)
        Asserted for one clock to indicate a packet has finished transmission.

    o_usbp : Signal(1)
        Raw value of USB+ line.

    o_usbn : Signal(1)
        Raw value of USB- line.

    o_oe : Signal(1)
        When asserted it indicates that the tx pipeline should be driving USB.
    """

    def __init__(self, i_bit_strobe):
        #, i_pkt_start=Signal(1), i_pid=Signal(4),
        #         i_token_payload=Constant(0, 11), i_data_valid=Signal(1),
        #         i_data_payload=Signal(8)):

        self.i_pkt_start     = i_pkt_start     = Signal(1)
        self.i_pid           = i_pid           = Signal(4)
        self.i_token_payload = i_token_payload = Constant(0, 11)
        self.i_data_valid    = i_data_valid    = Signal(1)
        self.i_data_payload  = i_data_payload  = Signal(8)

        self.submodules.pkt = pkt = FSM()

        bitstuff_stall = Signal(1)
        pkt_active = Signal(1)
        shift_sync = Signal(1)
        shift_pid = Signal(1)
        shift_eop = Signal(1)
        load_data = Signal(1)
        shift_data = Signal(1)
        load_crc16 = Signal(1)
        shift_crc16 = Signal(1)
        pkt_end = Signal(1)

        # the sync shifter is responsible for generating the packet sync.
        # it shifts out its data first.
        self.submodules.sync_shifter = sync_shifter = TxShifter(
            width = 8,
            i_put = i_pkt_start,
            i_shift = shift_sync & i_bit_strobe & ~bitstuff_stall,
            i_data = Constant(0b10000000, 8)
        )

        # the pid shifter shifts out the packet pid and complementary pid.
        # the pid is shifted out when the sync is complete.
        self.submodules.pid_shifter = pid_shifter = TxShifter(
            width = 8,
            i_put = i_pkt_start,
            i_shift = shift_pid & i_bit_strobe & ~bitstuff_stall,
            i_data = Cat(i_pid, 0b1111 ^ i_pid)
        )

        # the data shifter shifts out the data
        # the data is shifted out when the pid is complete.
        self.submodules.data_shifter = data_shifter = TxShifter(
            width = 8,
            i_put = load_data,
            i_shift = shift_data & i_bit_strobe & ~bitstuff_stall,
            i_data = i_data_payload
        )

        # generate crc16
        self.submodules.crc16_generator = crc16_generator = TxCrcGenerator(
            width      = 16,
            polynomial = 0b1000000000000101,
            initial    = 0b1111111111111111,

            i_reset = i_pkt_start,
            i_data = data_shifter.o_data,
            i_shift = shift_data & i_bit_strobe & ~bitstuff_stall
        )

        # the crc16 shifter shifts out the crc16 field.
        self.submodules.crc16_shifter = crc16_shifter = TxShifter(
            width = 16,
            i_put = load_crc16,
            i_shift = shift_crc16 & i_bit_strobe & ~bitstuff_stall,
            i_data = crc16_generator.o_crc
        )

        # calculate some values for the FSM
        pid_is_data = Signal(1)

        self.sync += [
            If(i_pkt_start,
                pid_is_data.eq(i_pid[0:2] == 0b11)
            )
        ]

        pkt.act("IDLE",
            If(i_pkt_start,
                NextState("SYNC")
            )
        )

        pkt.act("SYNC",
            pkt_active.eq(1),
            shift_sync.eq(1),

            If(sync_shifter.o_empty,
                NextState("PID")
            )
        )

        pkt.act("PID",
            pkt_active.eq(1),
            shift_pid.eq(1),

            If(pid_shifter.o_empty,
                If(pid_is_data,
                    If(i_data_valid,
                        load_data.eq(1),
                        NextState("DATA")
                    ).Else(
                        load_crc16.eq(1),
                        NextState("CRC16")
                    )
                ).Else(
                    NextState("EOP_0")
                )
            )
        )

        pkt.act("DATA",
            pkt_active.eq(1),
            shift_data.eq(1),

            If(data_shifter.o_empty,
                If(i_data_valid,
                    load_data.eq(1)
                ).Else(
                    load_crc16.eq(1),
                    NextState("CRC16")
                )
            )
        )

        pkt.act("CRC16",
            pkt_active.eq(1),
            shift_crc16.eq(1),

            If(crc16_shifter.o_empty,
                NextState("EOP_0")
            )
        )

        pkt.act("EOP_0",
            pkt_active.eq(1),
            shift_eop.eq(1),

            If(i_bit_strobe,
                NextState("EOP_1")
            )
        )

        pkt.act("EOP_1",
            pkt_active.eq(1),
            shift_eop.eq(1),

            If(i_bit_strobe,
                pkt_end.eq(1),
                NextState("IDLE")
            )
        )


        ######################################################################
        #
        # Mux shifter output together and select based on pkt state machine.
        #
        mux_stuff_oe = Signal(1)
        mux_stuff_data = Signal(1)
        mux_stuff_se0 = Signal(1)
        mux_stuff_bit_strobe = Signal(1)

        self.sync += [
            mux_stuff_bit_strobe.eq(i_bit_strobe),

            mux_stuff_oe.eq(pkt_active),

            mux_stuff_se0.eq(0),
            mux_stuff_data.eq(0),

            If(shift_sync,
                mux_stuff_se0.eq(0),
                mux_stuff_data.eq(sync_shifter.o_data),

            ).Elif(shift_pid,
                mux_stuff_se0.eq(0),
                mux_stuff_data.eq(pid_shifter.o_data),

            ).Elif(shift_data,
                mux_stuff_se0.eq(0),
                mux_stuff_data.eq(data_shifter.o_data),

            ).Elif(shift_crc16,
                mux_stuff_se0.eq(0),
                mux_stuff_data.eq(crc16_shifter.o_data),

            ).Elif(shift_eop,
                mux_stuff_se0.eq(1),
                mux_stuff_data.eq(0),
            )
        ]


        ######################################################################
        #
        # Bitstuff as necessary
        #
        self.submodules.bitstuffer = bitstuffer = TxBitstuffer(
            i_valid = mux_stuff_bit_strobe,
            i_oe = mux_stuff_oe,
            i_data = mux_stuff_data,
            i_se0 = mux_stuff_se0
        )

        self.comb += [
             bitstuff_stall.eq(bitstuffer.o_stall)
        ]


        ######################################################################
        #
        # NRZI Encoding
        #
        self.submodules.nrzi = nrzi = TxNrziEncoder(
            i_valid = mux_stuff_bit_strobe,
            i_oe = bitstuffer.o_oe,
            i_data = bitstuffer.o_data,
            i_se0 = bitstuffer.o_se0
        )


        ######################################################################
        #
        # Flop all outputs
        #
        self.o_data_get = Signal(1)
        self.o_pkt_end = Signal(1)
        self.o_usbp = Signal(1)
        self.o_usbn = Signal(1)
        self.o_oe = Signal(1)

        self.sync += [
            self.o_data_get.eq(load_data),
            self.o_pkt_end.eq(pkt_end),
            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe)
        ]


###############################################################################
###############################################################################
###############################################################################
######
###### USB Device Core
######
###############################################################################
###############################################################################
###############################################################################


class Raw(Instance.PreformattedParam):
    def __init__(self, value):
        self.value = value


class UsbIoBuf(Module):
    def __init__(self, usbp_pin, usbn_pin, usb_pullup_pin=None):
        # tx/rx io interface
        self.usb_tx_en = Signal()
        self.usb_p_tx = Signal()
        self.usb_n_tx = Signal()

        self.usb_p_rx = Signal()
        self.usb_n_rx = Signal()

        self.usb_p_rx_io = Signal()
        self.usb_n_rx_io = Signal()

        self.usb_pullup = Signal()
        if usb_pullup_pin is not None:
            self.comb += [
                usb_pullup_pin.eq(self.usb_pullup),
            ]

        #######################################################################
        #######################################################################
        #### Mux the USB +/- pair with the TX and RX paths
        #######################################################################
        #######################################################################
        self.comb += [
            If(self.usb_tx_en,
                self.usb_p_rx.eq(0b1),
                self.usb_n_rx.eq(0b0)
            ).Else(
                self.usb_p_rx.eq(self.usb_p_rx_io),
                self.usb_n_rx.eq(self.usb_n_rx_io)
            )
        ]

        self.specials += [
            Instance(
                "SB_IO",
                p_PIN_TYPE = Raw("6'b101001"),
                p_PULLUP = 0b0,

                io_PACKAGE_PIN = usbp_pin,
                i_OUTPUT_ENABLE = self.usb_tx_en,
                i_D_OUT_0 = self.usb_p_tx,
                o_D_IN_0 = self.usb_p_rx_io
            ),

            Instance(
                "SB_IO",
                p_PIN_TYPE = Raw("6'b101001"),
                p_PULLUP = 0b0,

                io_PACKAGE_PIN = usbn_pin,
                i_OUTPUT_ENABLE = self.usb_tx_en,
                i_D_OUT_0 = self.usb_n_tx,
                o_D_IN_0 = self.usb_n_rx_io
            )
        ]


class PID(IntEnum):
    # USB Packet IDs
    OUT     = 0b0001
    IN      = 0b1001
    SOF     = 0b0101
    SETUP   = 0b1101
    DATA0   = 0b0011
    DATA1   = 0b1011
    ACK     = 0b0010
    NAK     = 0b1010
    STALL   = 0b1110


class UsbSimpleFifo(Module, AutoCSR):

    def __init__(self, iobuf):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.rx = ev.EventSourcePulse()

        # ---------------------
        # RX side
        # ---------------------
        self.submodules.rx = rx = ClockDomainsRenamer("usb_48")(
            RxPacketSimple(iobuf.usb_p_rx, iobuf.usb_n_rx))

        obuf = fifo.AsyncFIFOBuffered(width=8, depth=128)
        self.submodules.obuf = ClockDomainsRenamer({"write": "usb_48", "read": "sys"})(obuf)

        # USB side (writing)
        self.comb += [
            self.obuf.din.eq(self.rx.o_data_payload),
            self.obuf.we.eq(self.rx.o_data_put),
            self.ev.rx.trigger.eq(self.rx.o_pkt_end),
        ]

        # System side (reading)
        self.submodules.obuf_head = CSR(8)
        self.submodules.obuf_empty = CSRStatus(1)
        self.comb += [
            self.obuf_head.w.eq(self.obuf.dout),
            self.obuf.re.eq(self.obuf_head.re),
            self.obuf_empty.status.eq(~self.obuf.readable),
        ]

        # ---------------------
        # TX side
        # ---------------------
        self.submodules.tx = tx = ClockDomainsRenamer("usb_48")(
            TxPacketSimple(i_bit_strobe = rx.o_bit_strobe))

        ibuf = fifo.AsyncFIFOBuffered(width=8, depth=128)
        self.submodules.ibuf = ClockDomainsRenamer({"write": "sys", "read": "usb_48"})(ibuf)

        # System side (writing)
        self.submodules.arm = CSRStorage(1)
        self.submodules.ibuf_head = CSR(8)
        self.submodules.ibuf_empty = CSRStatus(1)
        self.comb += [
            self.ibuf.din.eq(self.ibuf_head.r),
            self.ibuf.we.eq(self.ibuf_head.re),
            self.ibuf_empty.status.eq(~self.ibuf.readable & ~tx.pkt_active),
        ]

        # USB side (reading)
        self.comb += [
            tx.i_more_data.eq(self.ibuf.readable & self.arm.storage),
            tx.i_data.eq(self.ibuf.dout),
            self.ibuf.re.eq(tx.shifter.o_empty & self.arm.storage),
        ]

        # ----------------------
        # Tristate
        # ----------------------
        self.submodules.iobuf = iobuf
        self.comb += [
            iobuf.usb_tx_en.eq(tx.o_oe),
            iobuf.usb_p_tx.eq(tx.o_usbp),
            iobuf.usb_n_tx.eq(tx.o_usbn),
        ]
        self.submodules.pullup = GPIOOut(iobuf.usb_pullup)


# Token
# Data
# Handshake

class UsbCore(Module):
    def __init__(self, iobuf):
        self.submodules.iobuf = iobuf

        #### RX Phy
        self.submodules.rx = rx = UsbFsRx(
            usbp_raw = self.iobuf.usb_p_rx,
            usbn_raw = self.iobuf.usb_n_rx
        )

        #### TX Phy
        self.submodules.tx = tx = UsbFsTx(
            _bit_strobe = rx.o_bit_strobe,
        )

        self.comb += [
            self.iobuf.usb_tx_en.eq(tx.o_oe),
            self.iobuf.usb_p_tx.eq(tx.o_usbp),
            self.iobuf.usb_n_tx.eq(tx.o_usbn),
        ]

        self.reset = Signal()

        self.transfer_tok    = Signal(4)    # Contains the transfer token type
        self.transfer_start  = Signal()     # Asserted when a transfer is starting
        self.transfer_setup  = Signal()     # Asserted when a transfer is a setup
        self.transfer_commit = Signal()     # Asserted when a transfer succeeds
        self.transfer_abort  = Signal()     # Asserted when a transfer fails
        self.transfer_end    = Signal()     # Asserted when transfer ends
        self.comb += [
            self.transfer_end.eq(self.transfer_commit | self.transfer_abort),
        ]

        self.fast_ep_num  = Signal(4)
        self.fast_ep_dir  = Signal()
        self.fast_ep_addr = Signal(5)
        self.ep_addr = Signal(5)
        self.ep_num = self.ep_addr[1:]
        self.comb += [
            self.fast_ep_num.eq(self.rx.decode.o_ep),
            self.fast_ep_dir.eq(self.rx.decode.o_pid == PID.IN),
            self.fast_ep_addr.eq(Cat(self.fast_ep_dir, self.fast_ep_num)),
        ]

        #self.data_recv_ready   = Signal()   # Assert when ready to receive data.
        self.data_recv_put     = Signal()   # Toggled when data is received.
        self.data_recv_payload = Signal(8)

        self.data_send_have    = Signal()   # Assert when data is available.
        self.data_send_get     = Signal()   # Toggled when data is sent.
        self.data_send_payload = Signal(8)

        pkt_end = Signal()
        self.comb += [
            pkt_end.eq(self.rx.o_pkt_end | self.tx.o_pkt_end),
        ]

        response_pid = Signal(4)

        self.dtb = Signal()
        self.arm = Signal()
        self.sta = Signal()

        # Host->Device data path (Out + Setup data path)
        #
        # Setup --------------------
        # >Setup
        # >Data0[bmRequestType, bRequest, wValue, wIndex, wLength]
        # <Ack
        # --------------------------
        #
        # Data ---------------------
        # >Out        >Out        >Out
        # >DataX[..]  >DataX[..]  >DataX
        # <Ack        <Nak        <Stall
        #
        # Status -------------------
        # >Out
        # >Data0[]
        # <Ack
        # ---------------------------
        #
        # Host<-Device data path (In data path)
        # --------------------------
        # >In         >In     >In
        # <DataX[..]  <Stall  <Nak
        # >Ack
        # ---------------------------
        # >In
        # <Data0[]
        # >Ack
        # ---------------------------
        self.submodules.transfer = transfer = FSM(reset_state="WAIT_TOKEN")
        transfer.act("ERROR",
            If(self.reset, NextState("WAIT_TOKEN")),
        )

        transfer.act("WAIT_TOKEN",
            If(self.rx.o_pkt_start, NextState("RECV_TOKEN")),
        )

        transfer.act("RECV_TOKEN",
            self.transfer_start.eq(1),
            If(pkt_end,
                NextValue(self.ep_addr, self.fast_ep_addr),
                NextValue(self.transfer_tok, self.rx.decode.o_pid),
                #If(self.rx.decode.o_addr != addr, NextState("IGNORE")),

                If(rx.decode.o_pid == PID.SETUP,
                    NextValue(response_pid, PID.ACK),
                ).Else(
                    If(self.sta,
                        NextValue(response_pid, PID.STALL),
                    ).Elif(self.arm,
                        NextValue(response_pid, PID.ACK),
                    ).Else(
                        NextValue(response_pid, PID.NAK),
                    ),
                ),

                # Setup transfer
                If(rx.decode.o_pid == PID.SETUP,
                    NextState("RECV_DATA"),

                # Out transfer
                ).Elif(rx.decode.o_pid == PID.OUT,
                    NextState("RECV_DATA"),

                # In transfer
                ).Elif(rx.decode.o_pid == PID.IN,
                    If(~self.arm,
                        NextState("SEND_HAND"),
                    ).Else(
                        NextState("SEND_DATA"),
                    ),
                ).Else(
                    NextState("WAIT_TOKEN"),
                ),
            ),
        )

        # Out + Setup pathway
        transfer.act("RECV_DATA",
            If(response_pid == PID.ACK,
                self.data_recv_put.eq(self.rx.decode.put_data),
            ),
            If(pkt_end, NextState("SEND_HAND")),
        )
        self.comb += [
            self.data_recv_payload.eq(self.rx.decode.data_n1),
        ]

        # In pathway
        transfer.act("SEND_DATA",
            self.data_send_get.eq(self.tx.o_data_get),
            If(pkt_end, NextState("RECV_HAND")),
        )
        self.comb += [
            self.tx.i_data_valid.eq(self.data_send_have),
            self.tx.i_data_payload.eq(self.data_send_payload),
        ]

        # Handshake
        transfer.act("RECV_HAND",
            # Host can't reject?
            self.transfer_commit.eq(1),
            If(pkt_end, NextState("WAIT_TOKEN")),
        )
        transfer.act("SEND_HAND",
            self.transfer_setup.eq(self.transfer_tok == (PID.SETUP >> 2)),
            If(response_pid == PID.ACK,
                self.transfer_commit.eq(1),
            ).Else(
                self.transfer_abort.eq(1),
            ),
            If(pkt_end, NextState("WAIT_TOKEN")),
        )

        # Code to initiate the sending of packets when entering the SEND_XXX
        # states.
        self.comb += [
            If(transfer.after_entering("SEND_DATA"),
                If(self.dtb,
                    self.tx.i_pid.eq(PID.DATA1),
                ).Else(
                    self.tx.i_pid.eq(PID.DATA0),
                ),
                self.tx.i_pkt_start.eq(1),
            ),
            If(transfer.after_entering("SEND_HAND"),
                self.tx.i_pid.eq(response_pid),
                self.tx.i_pkt_start.eq(1),
            ),
        ]


        # --------------------------


class EndpointType(IntEnum):
    IN = 1
    OUT = 2
    BIDIR = IN | OUT

    @classmethod
    def epaddr(cls, ep_num, ep_dir):
        assert ep_dir != cls.BIDIR
        return ep_num << 1 | (ep_dir == cls.IN)

    @classmethod
    def epnum(cls, ep_addr):
        return ep_addr >> 1

    @classmethod
    def epdir(cls, ep_addr):
        if ep_addr & 0x1 == 0:
            return cls.OUT
        else:
            return cls.IN


class EndpointResponse(IntEnum):
    # Clearing top bit of STALL -> NAK
    STALL = 0b11
    ACK   = 0b00
    NAK   = 0b01
    NONE  = 0b10


class FakeFifo(Module):
    def __init__(self):
        self.din = Signal(8)
        self.writable = Signal(1, reset=1)
        self.we = Signal(1, reset=1)

        self.dout = Signal(8)
        self.readable = Signal(1, reset=1)
        self.re = Signal(1, reset=1)


class Endpoint(Module, AutoCSR):
    def __init__(self):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.error = ev.EventSourcePulse()
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()

        self.trigger = self.ev.packet.trigger

        # Last PID?
        self.last_tok = CSRStatus(2)

        # How to respond to requests;
        #  - 10 - No response
        #  - 00 - ACK
        #  - 01 - NAK
        #  - 11 - STALL
        self.submodules.respond = CSRStorage(2, write_from_dev=True)

        self.response = Signal(2)
        self.reset = Signal()
        self.comb += [
            self.response.eq(Cat(
                    self.respond.storage[0] | self.ev.packet.pending,
                    self.respond.storage[1],
            )),
        ]
        self.comb += [
            self.respond.dat_w.eq(EndpointResponse.NAK),
            self.respond.we.eq(self.reset),
        ]

        self.submodules.dtb = CSRStorage(1, write_from_dev=True)
        self.comb += [
            self.dtb.dat_w.eq(~self.dtb.storage | self.reset),
        ]
        # When triggered, flip the data toggle bit
        toggle = Signal()
        self.sync += [
            If(self.trigger | self.reset,
                If(~toggle,
                    toggle.eq(1),
                    self.dtb.we.eq(1),
                ).Else(
                    self.dtb.we.eq(0),
                ),
            ).Else(
                toggle.eq(0),
            ),
        ]

class EndpointNone(Module):
    def __init__(self):
        self.ibuf = FakeFifo()
        self.obuf = FakeFifo()
        self.response = Signal(reset=EndpointResponse.NAK)
        self.trigger = Signal()
        self.reset = Signal()

        self.last_tok = Module()
        self.last_tok.status = Signal(2)

        self.dtb = Module()
        self.dtb.storage = Signal()


class EndpointIn(Module, AutoCSR):
    """Endpoint for Device->Host data.

    Reads from the buffer memory.
    Raises packet IRQ when packet has been sent.
    CPU writes to the head CSRT to push data onto the FIFO.
    """
    def __init__(self):
        Endpoint.__init__(self)

        ibuf = fifo.AsyncFIFOBuffered(width=8, depth=512)
        self.submodules.ibuf = ClockDomainsRenamer({"write": "sys", "read": "usb_48"})(ibuf)

        self.ibuf_head = CSR(8)
        self.ibuf_empty = CSRStatus(1)
        self.comb += [
            self.ibuf.din.eq(self.ibuf_head.r),
            self.ibuf.we.eq(self.ibuf_head.re),
            self.ibuf_empty.status.eq(~self.ibuf.readable),
        ]
        self.obuf = FakeFifo()


class EndpointOut(Module, AutoCSR):
    """Endpoint for Host->Device data.

    Raises packet IRQ when new packet has arrived.
    CPU reads from the head CSR to get front data from FIFO.
    CPU writes to head CSR to advance the FIFO by one.
    """
    def __init__(self):
        Endpoint.__init__(self)

        outbuf = fifo.AsyncFIFOBuffered(width=8, depth=512)
        self.submodules.obuf = ClockDomainsRenamer({"write": "usb_48", "read": "sys"})(outbuf)

        self.obuf_head = CSR(8)
        self.obuf_empty = CSRStatus(1)
        self.comb += [
            self.obuf_head.w.eq(self.obuf.dout),
            self.obuf.re.eq(self.obuf_head.re),
            self.obuf_empty.status.eq(~self.obuf.readable),
        ]
        self.ibuf = FakeFifo()


class UsbDeviceCpuInterface(Module, AutoCSR):
    """
    Implements the SW->HW interface for UsbDevice.
    """

    def __init__(self, iobuf, endpoints=[EndpointType.BIDIR, EndpointType.IN, EndpointType.BIDIR]):
        size = 9

        self.iobuf = iobuf

        self.submodules.pullup = GPIOOut(iobuf.usb_pullup)

        # USB Core
        self.submodules.usb_core = ClockDomainsRenamer("usb_48")(UsbCore(iobuf))

        # Endpoint controls
        ems = []
        eps = []
        trigger_all = []
        for i, endp in enumerate(endpoints):
            if endp & EndpointType.OUT:
                exec("self.submodules.ep_%s_out = ep = EndpointOut()" % i)
                oep = getattr(self, "ep_%s_out" % i)
                ems.append(oep.ev)
            else:
                oep = EndpointNone()

            trigger_all.append(oep.trigger.eq(1)),
            eps.append(oep)

            if endp & EndpointType.IN:
                exec("self.submodules.ep_%s_in = ep = EndpointIn()" % i)
                iep = getattr(self, "ep_%s_in" % i)
                ems.append(iep.ev)
            else:
                iep = EndpointNone()

            trigger_all.append(iep.trigger.eq(1)),
            eps.append(iep)

        self.submodules.ev = ev.SharedIRQ(*ems)

        self.eps = Array(eps)

        transfer_commit = Signal()
        self.specials += cdc.MultiReg(self.usb_core.transfer_commit, transfer_commit, n=2)

        ep0out_addr = EndpointType.epaddr(0, EndpointType.OUT)
        ep0in_addr = EndpointType.epaddr(0, EndpointType.IN)

        # Setup packet causes ep0 in and ep0 out to reset
        self.comb += [
            self.eps[ep0out_addr].reset.eq(self.usb_core.transfer_setup),
            self.eps[ep0in_addr].reset.eq(self.usb_core.transfer_setup),
        ]

        self.comb += [
            # This needs to be correct *before* token is finished, everything
            # else uses registered outputs.
            self.usb_core.transfer_resp.eq(self.eps[self.usb_core.fast_ep_addr].response),

            # Control signals
            If(~iobuf.usb_pullup,
                *trigger_all,
            ).Else(
                self.eps[self.usb_core.ep_addr].trigger.eq(transfer_commit),
                self.usb_core.dtb.eq(self.eps[self.usb_core.ep_addr].dtb.storage),
            ),
            # FIFO
            # Host->Device[Out Endpoint] pathway
            self.usb_core.data_recv_ready.eq(self.eps[self.usb_core.ep_addr].obuf.writable),
            self.eps[self.usb_core.ep_addr].obuf.we.eq(self.usb_core.data_recv_put),
            self.eps[self.usb_core.ep_addr].obuf.din.eq(self.usb_core.data_recv_payload),
            # [In Endpoint]Device->Host pathway
            self.usb_core.data_send_have.eq(self.eps[self.usb_core.ep_addr].ibuf.readable),
            self.usb_core.data_send_payload.eq(self.eps[self.usb_core.ep_addr].ibuf.dout),
            self.eps[self.usb_core.ep_addr].ibuf.re.eq(self.usb_core.data_send_get),
        ]

        self.sync += [
            If(transfer_commit,
                self.eps[self.usb_core.ep_addr].last_tok.status.eq(self.usb_core.transfer_tok),
            ),
        ]


class UsbDeviceCpuMemInterface(Module, AutoCSR):

    def csr_bits(self, csr):
        l = value_bits_sign(csr.storage)[0]
        bits = [Signal() for i in range(l)]
        self.comb += [bits[i].eq(csr.storage[i]) for i in range(l)]
        return Array(bits)

    def __init__(self, iobuf, num_endpoints=3, depth=512):

        ptr_width = 9 # Signal(max=depth).size

        self.iobuf = iobuf
        self.submodules.pullup = GPIOOut(iobuf.usb_pullup)
        self.submodules.usb = ClockDomainsRenamer("usb_48")(UsbCore(iobuf))

        #self.submodules.packet = ev.EventManager()
        #self.packet.setup = ev.EventSourcePulse()
        #self.submodules.setup_ptr = CSRStatus(ptr_width)

        # Output endpoints
        all_trig = []
        trig = []

        self.submodules.packet = ev.EventManager()
        for i in range(0, num_endpoints):
            exec("self.packet.oep{} = ev.EventSourcePulse()".format(i))
            t = getattr(self.packet, "oep{}".format(i)).trigger
            all_trig.append(t.eq(1))
            trig.append(t)

            exec("self.packet.iep{} = ev.EventSourcePulse()".format(i))
            t = getattr(self.packet, "iep{}".format(i)).trigger
            all_trig.append(t.eq(1))
            trig.append(t)

        self.packet.finalize()

        l = num_endpoints * 2

        self.submodules.sta = CSRStorage(l)                         # Stall endpoint
        self.submodules.dtb = CSRStorage(l, write_from_dev=True)    # Data toggle bit
        self.submodules.arm = CSRStorage(l)                         # Endpoint is ready

        self.comb += [
            self.usb.sta.eq(self.csr_bits(self.sta)[self.usb.fast_ep_addr]),
            self.usb.dtb.eq(self.csr_bits(self.dtb)[self.usb.fast_ep_addr]),
            self.usb.arm.eq(self.csr_bits(self.arm)[self.usb.fast_ep_addr]), # & Array(self.packet.pending.r)[self.usb.fast_ep_addr]),
            If(~iobuf.usb_pullup,
                *all_trig,
            ).Else(
                Array(trig)[self.usb.ep_addr].eq(self.usb.transfer_commit),
            ),
        ]

        # Output pathway
        # -----------------------
        self.specials.obuf = Memory(8, depth)
        self.specials.oport_wr = self.obuf.get_port(write_capable=True, clock_domain="usb_48")
        self.specials.oport_rd = self.obuf.get_port(clock_domain="sys")

        optrs = []
        for i in range(0, num_endpoints):
            exec("self.submodules.optr_ep{0} = CSRStatus(ptr_width, name='optr_ep{0}')".format(i))
            optrs.append(getattr(self, "optr_ep{}".format(i)).status)

        self.obuf_ptr = Signal(ptr_width)
        self.comb += [
            self.oport_wr.adr.eq(self.obuf_ptr),
            self.oport_wr.dat_w.eq(self.usb.data_recv_payload),
            self.oport_wr.we.eq(self.usb.data_recv_put),
        ]
        # On a commit, copy the current obuf_ptr to the CSR register.
        self.sync += [
            If(self.usb.transfer_commit,
                If((self.usb.transfer_tok == PID.OUT) | (self.usb.transfer_tok == PID.SETUP),
                    Array(optrs)[self.usb.ep_num].eq(self.obuf_ptr),
                ),
            ),
        ]
        self.sync.usb_48 += [
            If(self.usb.data_recv_put, self.obuf_ptr.eq(self.obuf_ptr + 1)),
        ]

        # Input pathway
        # -----------------------
        self.specials.ibuf = Memory(8, depth)
        self.specials.iport_wr = self.ibuf.get_port(write_capable=True, clock_domain="sys")
        self.specials.iport_rd = self.ibuf.get_port(clock_domain="usb_48")

        #for i in range(0, num_endpoints):
        #    exec("self.submodules.iptr_ep{0} = CSRStorage(ptr_width, name='iptr_ep{0}')".format(i))
        #    iptrs.append(getattr(self, "iptr_ep{}".format(i)).storage)
        #
        #    exec("self.submodules.ilen_ep{0} = CSRStorage(ptr_width, name='ilen_ep{0}')".format(i))
        #    ilens.append(getattr(self, "ilen_ep{}".format(i)).storage)
        assert num_endpoints == 3
        self.submodules.iptr_ep0 = CSRStorage(ptr_width)
        self.submodules.ilen_ep0 = CSRStorage(ptr_width)
        self.submodules.iptr_ep1 = CSRStorage(ptr_width)
        self.submodules.ilen_ep1 = CSRStorage(ptr_width)
        self.submodules.iptr_ep2 = CSRStorage(ptr_width)
        self.submodules.ilen_ep2 = CSRStorage(ptr_width)
        iptrs = [self.iptr_ep0.storage,self.iptr_ep1.storage,self.iptr_ep2.storage]
        ilens = [self.ilen_ep0.storage,self.ilen_ep1.storage,self.ilen_ep2.storage]

        self.ibuf_ptr = Signal(ptr_width)
        self.comb += [
            self.iport_rd.adr.eq(self.ibuf_ptr),
            self.usb.data_send_payload.eq(self.iport_rd.dat_r),
            #self.iport_rd.re.eq(),
        ]
        # On a transfer start, copy the CSR register into ibuf_ptr
        self.sync += [
            If(self.usb.transfer_start,
                self.ibuf_ptr.eq(Array(iptrs)[self.usb.fast_ep_num]),
            ),
        ]
        self.sync.usb_48 += [
            If(self.usb.data_send_get, self.ibuf_ptr.eq(self.ibuf_ptr + 1)),
        ]
        self.comb += [
            self.usb.data_send_have.eq(self.ibuf_ptr != Array(ilens)[self.usb.ep_num]),
        ]
