#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fifo
from migen.genlib import cdc

from litex.soc.interconnect import stream
from litex.soc.interconnect import wishbone
from litex.soc.interconnect import csr_eventmanager as ev

###############################################################################
###############################################################################
###############################################################################
######
###### Physical Layer Receive Path
######
###############################################################################
###############################################################################
###############################################################################

class RxClockDataRecovery(Module):
    """
    RxClockDataRecovery synchronizes the USB differential pair with the FPGAs
    clocks, de-glitches the differential pair, and recovers the incoming clock
    and data.

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    usbp_raw : Signal(1)
        Raw USB+ input from the FPGA IOs, no need to synchronize.

    usbn_raw : Signal(1)
        Raw USB- input from the FPGA IOs, no need to synchronize.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.
    The line_state_dj/dk/se0/se1 outputs are 1-hot encoded.

    line_state_valid : Signal(1)
        Asserted for one clock when the output line state is ready to be sampled.

    line_state_dj : Signal(1)
        Represents Full Speed J-state on the incoming USB data pair.
        Qualify with line_state_valid.

    line_state_dk : Signal(1)
        Represents Full Speed K-state on the incoming USB data pair.
        Qualify with line_state_valid.

    line_state_se0 : Signal(1)
        Represents SE0 on the incoming USB data pair.
        Qualify with line_state_valid.

    line_state_se1 : Signal(1)
        Represents SE1 on the incoming USB data pair.
        Qualify with line_state_valid.
    """
    def __init__(self, usbp_raw, usbn_raw):
        #######################################################################
        # Synchronize raw USB signals
        #
        # We need to synchronize the raw USB signals with the usb_48 clock
        # domain.  MultiReg implements a multi-stage shift register that takes
        # care of this for us.  Without MultiReg we would have metastability
        # issues.
        #
        usbp = Signal()
        usbn = Signal()

        self.specials += cdc.MultiReg(usbp_raw, usbp, n=3)
        self.specials += cdc.MultiReg(usbn_raw, usbn, n=3)


        #######################################################################
        # Line State Recovery State Machine
        #
        # The receive path doesn't use a differential receiver.  Because of
        # this there is a chance that one of the differential pairs will appear
        # to have changed to the new state while the other is still in the old
        # state.  The following state machine detects transitions and waits an
        # extra sampling clock before decoding the state on the differential
        # pair.  This transition period # will only ever last for one clock as
        # long as there is no noise on the line.  If there is enough noise on
        # the line then the data may be corrupted and the packet will fail the
        # data integrity checks.
        #
        self.submodules.lsr = lsr = FSM()

        dpair = Signal(2)
        self.comb += dpair.eq(Cat(usbn, usbp))

        # output signals for use by the clock recovery stage
        line_state_dt = Signal()
        line_state_dj = Signal()
        line_state_dk = Signal()
        line_state_se0 = Signal()
        line_state_se1 = Signal()

        # If we are in a transition state, then we can sample the pair and
        # move to the next corresponding line state.
        lsr.act("DT",
            line_state_dt.eq(1),
            Case(dpair, {
                0b10 : NextState("DJ"),
                0b01 : NextState("DK"),
                0b00 : NextState("SE0"),
                0b11 : NextState("SE1")
            })
        )

        # If we are in a valid line state and the value of the pair changes,
        # then we need to move to the transition state.
        lsr.act("DJ",  line_state_dj.eq(1),  If(dpair != 0b10, NextState("DT")))
        lsr.act("DK",  line_state_dk.eq(1),  If(dpair != 0b01, NextState("DT")))
        lsr.act("SE0", line_state_se0.eq(1), If(dpair != 0b00, NextState("DT")))
        lsr.act("SE1", line_state_se1.eq(1), If(dpair != 0b11, NextState("DT")))


        #######################################################################
        # Clock and Data Recovery
        #
        # The DT state from the line state recovery state machine is used to align to
        # transmit clock.  The line state is sampled in the middle of the bit time.
        #
        # Example of signal relationships
        # -------------------------------
        # line_state        DT  DJ  DJ  DJ  DT  DK  DK  DK  DK  DK  DK  DT  DJ  DJ  DJ
        # line_state_valid  ________----____________----____________----________----____
        # bit_phase         0   0   1   2   3   0   1   2   3   0   1   2   0   1   2
        #

        line_state_phase = Signal(2)

        self.line_state_valid = Signal()
        self.line_state_dj = Signal()
        self.line_state_dk = Signal()
        self.line_state_se0 = Signal()
        self.line_state_se1 = Signal()

        self.sync += [
            self.line_state_valid.eq(line_state_phase == 1),

            If(line_state_dt,
                # re-align the phase with the incoming transition
                line_state_phase.eq(0),

                # make sure we never assert valid on a transition
                self.line_state_valid.eq(0),
            ).Else(
                # keep tracking the clock by incrementing the phase
                line_state_phase.eq(line_state_phase + 1)
            ),

            # flop all the outputs to help with timing
            self.line_state_dj.eq(line_state_dj),
            self.line_state_dk.eq(line_state_dk),
            self.line_state_se0.eq(line_state_se0),
            self.line_state_se1.eq(line_state_se1),
        ]



class RxNRZIDecoder(Module):
    """
    NRZI decode

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
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_dj : Signal(1)
        Indicates the bus is currently in a Full-Speed J-state.
        Qualified by valid.

    i_dk : Signal(1)
        Indicates the bus is currently in a Full-Speed K-state.
        Qualified by valid.

    i_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.

    o_valid : Signal(1)
        Qualifier for all of the output signals. Indicates one bit of valid
        data is present on the outputs.

    o_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    o_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.
    """

    def __init__(self, i_valid, i_dj, i_dk, i_se0):
        o_valid = Signal(1)
        o_data = Signal(1)


        # simple state machine decodes a JK transition as a '0' and no
        # transition as a '1'.  se0 is ignored.
        self.submodules.nrzi = nrzi = FSM()

        nrzi.act("DJ",
            If(i_valid,
                o_valid.eq(1),

                If(i_dj,
                    o_data.eq(1)
                ).Elif(i_dk,
                    o_data.eq(0),
                    NextState("DK")
                )
            )
        )

        nrzi.act("DK",
            If(i_valid,
                o_valid.eq(1),

                If(i_dj,
                    o_data.eq(0),
                    NextState("DJ")
                ).Elif(i_dk,
                    o_data.eq(1)
                )
            )
        )


        # pass all of the outputs through a pipe stage
        self.o_valid = Signal(1)
        self.o_data = Signal(1)
        self.o_se0 = Signal(1)

        self.sync += [
            self.o_se0.eq(i_se0),
            self.o_valid.eq(o_valid),
            self.o_data.eq(o_data),
        ]



class RxBitstuffRemover(Module):
    """
    Bitstuff Removal

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.  This extra bit is required to recover the
    clock, but it should not be passed on to higher layers in the device.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

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
    Output ports are data members of the module. All output ports are flopped.

    o_valid : Signal(1)
        Qualifier for all of the output signals. Indicates one bit of valid
        data is present on the outputs.

    o_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    o_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.

    o_bitstuff_error : Signal(1)
        Indicates there has been a bitstuff error. A bitstuff error occurs
        when there should be a stuffed '0' after 6 consecutive 1's; but instead
        of a '0', there is an additional '1'.  This is normal during IDLE, but
        should never happen within a packet.
        Qualified by valid.
    """

    def __init__(self, i_valid, i_data, i_se0):
        # This statemachine recognizes sequences of 6 bits and drops the 7th bit.
        # The fsm implements a counter in a series of several states.  This is
        # intentional to help absolutely minimize the levels of logic used.
        self.submodules.stuff = stuff = FSM()

        drop_bit = Signal(1)

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
            drop_bit.eq(1),
            If(i_valid,
                # Reset the bitstuff counter, drop the data.
                NextState("D0")
            )
        )

        # pass all of the outputs through a pipe stage
        self.o_valid = Signal(1)
        self.o_data = Signal(1)
        self.o_se0 = Signal(1)
        self.o_bitstuff_error = Signal(1)

        self.sync += [
            self.o_se0.eq(i_se0),
            self.o_valid.eq(i_valid & ~drop_bit),
            self.o_data.eq(i_data),
            self.o_bitstuff_error.eq(drop_bit & i_data)
        ]



class RxPacketDetect(Module):
    """
    Packet Detection

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
    """
    Shifter

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

    i_valid : Signal(1)
        Qualifier for serial input data. Indicates one clock of data is valid.

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

    o_full : Signal(1)
        Asserted while the register is full.

    o_put : Signal(1)
        Asserted for one clock once the register is full.
    """
    def __init__(self, width, i_valid, i_data, i_reset):
        # Instead of using a counter, we will use a sentinal bit in the shift
        # register to indicate when it is full.
        shift_reg = Signal(width + 1)

        self.o_full = Signal(1)

        # the register is full once the top bit is '1'
        self.comb += [
            self.o_full.eq(shift_reg[0])
        ]

        # shift valid incoming data in while not full
        self.sync += [
            If(i_reset,
                shift_reg.eq(1 << width),
            ).Else(
                If(i_valid & ~self.o_full,
                    shift_reg.eq(Cat(shift_reg[1:width + 1], i_data))
                )
            )
        ]

        self.o_output = Signal(width)

        self.comb += [
            self.o_output.eq(shift_reg[1:width + 1])
        ]

        self.o_put = Signal(1)

        self.sync += [
            self.o_put.eq((shift_reg[0:2] == 0b10) & i_valid)
        ]




class RxCrcChecker(Module):
    """
    CRC Checker

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

    i_valid : Signal(1)
        Qualifier for input data and se0 signals. Indicates one bit of valid
        data is present on those inputs.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    i_reset : Signal(1)
        Resets the CRC calculation back to the initial state.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_crc_good : Signal()
        Packet PID. Qualified with o_pkt_pid_good.
    """
    def __init__(self, width, polynomial, initial, residual, i_valid, i_data, i_reset):
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
        self.start_pid = Signal()
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
                self.start_pid.eq(1),
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
        self.o_pkt_end = Signal()
        self.sync += [
            self.o_pkt_end.eq(self.decode.pkt_end),
        ]

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
        shifter = Signal(width + 1)

        self.sync += [
            If(i_put,
                shifter.eq(Cat(i_data[0:width], C(1, 1)))
            ).Elif(i_shift,
                shifter.eq(shifter >> 1)
            )
        ]

        self.o_data = Signal(1)
        self.o_empty = Signal(1)
        not_empty = Signal(1)

        self.comb += [
            self.o_data.eq(shifter[0]),
            self.o_empty.eq(~not_empty)
        ]

        self.sync += [
            If((shifter[1:width + 1] == C(1, width)) & i_shift,
                not_empty.eq(0)
            ).Elif(i_put,
                not_empty.eq(1)
            )
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
        shoulde be asserted on USB. Qualified by o_valid.

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
        shoulde be asserted on USB. Qualified by o_valid.

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
            pkt_end.eq(1),

            If(i_bit_strobe,
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

from litex.soc.interconnect.csr import *


class UsbIoBuf(Module):
    def __init__(self, usbp_pin, usbn_pin):
        # tx/rx io interface
        self.usb_tx_en = Signal()
        self.usb_p_tx = Signal()
        self.usb_n_tx = Signal()

        self.usb_p_rx = Signal()
        self.usb_n_rx = Signal()

        self.usb_p_rx_io = Signal()
        self.usb_n_rx_io = Signal()

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


class EndpointType(IntEnum):
    IN = 1
    OUT = 2
    BIDIR = IN | OUT


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


class UsbCore(Module):
    def __init__(self, iobuf):
        self.submodules.iobuf = iobuf

        #### RX Phy
        self.submodules.usbfsrx = usbfsrx = UsbFsRx(
            usbp_raw = self.iobuf.usb_p_rx,
            usbn_raw = self.iobuf.usb_n_rx
        )

        #### TX Phy
        self.submodules.usbfstx = usbfstx = UsbFsTx(
            i_bit_strobe = usbfsrx.o_bit_strobe,
        )

        self.comb += [
            self.iobuf.usb_tx_en.eq(usbfstx.o_oe),
            self.iobuf.usb_p_tx.eq(usbfstx.o_usbp),
            self.iobuf.usb_n_tx.eq(usbfstx.o_usbn),
        ]

        # Tracks if we are sending Data0 or Data1
        self.last_sent_data = Signal()
        self.last_recv_data = Signal()

        self.submodules.pe = pe = FSM()

        self.current_addr = Signal(7)
        self.current_ep = Signal(4)
        self.comb += [
            self.current_addr.eq(self.usbfsrx.decode.o_addr),
            self.current_ep.eq(self.usbfsrx.decode.o_ep),
        ]

        self.data_recv_ready = Signal()     # Assert when ready to received data
        self.data_recv_put = Signal()       # Toggled when data is received
        self.data_recv_payload = Signal(8)  # Data received

        self.data_send_ready = Signal()     # Assert when data ready to send
        self.data_send_get = Signal()       # Toggled when data is sent
        self.data_send_payload = Signal(8)  # Data to be send

        self.comb += [
            # Host->Device, Input path
            self.data_recv_put.eq(self.usbfsrx.decode.put_data),
            self.data_recv_payload.eq(self.usbfsrx.decode.data_n1),
            # Device->Host, Output path
            self.data_send_get.eq(self.usbfstx.o_data_get),
            self.usbfstx.i_data_valid.eq(self.data_send_ready),
            self.usbfstx.i_data_payload.eq(self.data_send_payload),
        ]

        # Packet counter
        self.pkt_count = Signal(8)
        self.sync += [
            If(self.usbfsrx.o_pkt_end,
                self.pkt_count.eq(self.pkt_count + 1),
            ),
        ]

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
        pe.act("WAIT_TOKEN",
            #If(self.usbfsrx.decode.end_token,
            If(self.usbfsrx.o_pkt_end,
                If(self.usbfsrx.decode.o_pid == PID.SETUP,
                    NextState("RECV_DATA"),
                    NextValue(self.last_sent_data, 0),
                ).Elif(self.usbfsrx.decode.o_pid == PID.OUT,
                    NextState("RECV_DATA"),
                ).Elif(self.usbfsrx.decode.o_pid == PID.IN,
                    # Endpoint isn't ready
                    If(~self.data_send_ready,
                        self.usbfstx.i_pid.eq(PID.NAK),
                        self.usbfstx.i_pkt_start.eq(1),
                        NextState("SEND_HAND"),
                    ).Else(
                        If(self.last_sent_data,
                            self.usbfstx.i_pid.eq(PID.DATA0),
                            NextValue(self.last_sent_data, 0),
                        ).Else(
                            self.usbfstx.i_pid.eq(PID.DATA1),
                            NextValue(self.last_sent_data, 1),
                        ),
                        self.usbfstx.i_pkt_start.eq(1),
                        NextState("SEND_DATA"),
                    ),
                )
            ),
        )

        # Data packet
        pe.act("RECV_DATA",
            # Was the data path not ready, NAK the packet
            If(self.usbfsrx.decode.put_data & ~(self.data_recv_ready),
                NextState("WAIT_NAK"),
            ),
            # Packet finished
            If(self.usbfsrx.o_pkt_end,
                self.usbfstx.i_pid.eq(PID.ACK),
                self.usbfstx.i_pkt_start.eq(1),
                NextState("SEND_HAND"),
            ),
        )
        pe.act("SEND_DATA",
            If(self.usbfstx.o_pkt_end,
                NextState("RECV_HAND"),
            ),
        )
        # Handshake
        pe.act("RECV_HAND",
            If(self.usbfsrx.o_pkt_end,
                NextState("WAIT_TOKEN"),
            ),
        )
        pe.act("WAIT_NAK",
            If(~self.usbfsrx.decode.pkt_det.o_pkt_active,
                self.usbfstx.i_pid.eq(PID.NAK),
                self.usbfstx.i_pkt_start.eq(1),
                NextState("SEND_HAND"),
            ),
        )
        pe.act("SEND_HAND",
            If(self.usbfstx.o_pkt_end,
                NextState("WAIT_TOKEN"),
            ),
        )

        self.error = Signal()
        self.reset = Signal()
        pe.act("ERROR",
            self.error.eq(1),
            If(self.reset,
                NextState("WAIT_TOKEN"),
            ),
        )

        # --------------------------


class Endpoint(Module, AutoCSR):
    def __init__(self):
        self.submodules.ev = ev.EventManager()
        self.ev.error = ev.EventSourcePulse()
        self.ev.packet = ev.EventSourcePulse()
        self.ev.finalize()

        #self.respond = CSRStorage(1)
        # How to respond to requests;
        #  - 00 - No response
        #  - 01 - ACK
        #  - 11 - NAK
        #  - 10 - STALL
        #self.response = CSRStorage(2, write_from_dev=True)

        self.head = CSR(8)
        self.empty = CSRStatus(1)


class EndpointOut(Endpoint):
    """Endpoint for Host->Device data.

    Raises packet IRQ when new packet has arrived.
    CPU reads from the head CSR to get front data from FIFO.
    CPU writes to head CSR to advance the FIFO by one.
    """
    def __init__(self):
        Endpoint.__init__(self)

        buf = fifo.AsyncFIFOBuffered(width=8, depth=512)
        self.submodules.buf = ClockDomainsRenamer({"write": "usb_48", "read": "sys"})(buf)

        self.comb += [
            self.head.w.eq(self.buf.dout),
            self.buf.re.eq(self.head.re),
            self.empty.status.eq(~self.buf.readable),
            self.ev.packet.trigger.eq(self.buf.readable),
        ]


class EndpointIn(Endpoint):
    """Endpoint for Device->Host data.

    Reads from the buffer memory.
    Raises packet IRQ when packet has been sent.
    CPU writes to the head CSRT to push data onto the FIFO.
    """
    def __init__(self):
        Endpoint.__init__(self)

        buf = fifo.AsyncFIFOBuffered(width=8, depth=512)
        self.submodules.buf = ClockDomainsRenamer({"write": "sys", "read": "usb_48"})(buf)

        self.comb += [
            self.buf.din.eq(self.head.r),
            self.buf.we.eq(self.head.re),
            self.empty.status.eq(~self.buf.readable),
            self.ev.packet.trigger.eq(~self.buf.readable),
        ]


class EndpointEmpty(Module):
    def __init__(self):
        self.ev = object()
        self.ev.error = Signal()
        self.ev.packet = Signal()

        self.buf = object()

        self.buf.din = Signal(8)
        self.buf.writable = Signal(1)
        self.buf.we = Signal(1)

        self.buf.dout = Signal(8)
        self.buf.readable = Signal(1)
        self.buf.re = Signal(1)

class FifoFake(Module):
    def __init__(self):
        self.din = Signal(8)
        self.writable = Signal(1)
        self.we = Signal(1)

        self.dout = Signal(8)
        self.readable = Signal(1)
        self.re = Signal(1)



class UsbDeviceCpuInterface(Module, AutoCSR):
    """
    Implements the SW->HW interface for UsbDevice.
    """

    def __init__(self, iobuf, endpoints=[EndpointType.BIDIR]):
        size = 9

        self.iobuf = iobuf

        # USB Core
        self.submodules.usb_core = ClockDomainsRenamer("usb_48")(UsbCore(iobuf))

        self.pkt_count = CSRStatus(8)
        self.comb += [
            self.pkt_count.status.eq(self.usb_core.pkt_count),
        ]

        # Endpoint controls
        ep_ins = []
        ep_outs = []
        for i, endp in enumerate(endpoints):
            if endp & EndpointType.IN:
                exec("self.submodules.ep_%s_in = EndpointIn()" % i)
                ep_ins.append(getattr(self, "ep_%s_in" % i).buf)
            else:
                ep_ins.append(FifoFake())

            if endp & EndpointType.OUT:
                exec("self.submodules.ep_%s_out = EndpointOut()" % i)
                ep_outs.append(getattr(self, "ep_%s_out" % i).buf)
            else:
                ep_outs.append(FifoFake())

        self.ep_ins = Array(ep_ins)
        self.ep_outs = Array(ep_outs)

        self.comb += [
            # Host->Device[Out Endpoint] pathway
            self.usb_core.data_recv_ready.eq(self.ep_outs[self.usb_core.current_ep].writable),
            self.ep_outs[self.usb_core.current_ep].we.eq(self.usb_core.data_recv_put),
            self.ep_outs[self.usb_core.current_ep].din.eq(self.usb_core.data_recv_payload),
            # [In Endpoint]Device->Host pathway
            self.usb_core.data_send_ready.eq(self.ep_ins[self.usb_core.current_ep].readable),
            self.usb_core.data_send_payload.eq(self.ep_ins[self.usb_core.current_ep].dout),
            self.ep_ins[self.usb_core.current_ep].re.eq(self.usb_core.data_send_get),
        ]


class UsbDevice(Module):

    def __init__(self, iobuf, dev_addr, endpoints=[EndpointType.BIDIR]):
        num_endpoints = len(endpoints)

        self.submodules.iobuf = iobuf
        self.dev_addr = dev_addr

        self.endp_ins = []
        self.endp_outs = []
        for i, endp in enumerate(endpoints):
            ep_in = stream.Endpoint([("data", 8)], name="ep_%i_in" % i)
            ep_out = stream.Endpoint([("data", 8)], name="ep_%i_out" % i)

            if not (endp & EndpointType.IN):
                self.comb += [
                    ep_in.ready.eq(0),
                    ep_in.data.eq(0),
                    ep_in.first.eq(0),
                    ep_in.last.eq(0),
                ]

            if not (endp & EndpointType.OUT):
                self.comb += [
                    ep_out.ready.eq(0),
                    ep_out.data.eq(0),
                    ep_out.first.eq(0),
                    ep_out.last.eq(0),
                ]

            self.endp_ins.append(ep_in)
            self.endp_outs.append(ep_out)

        self.a_endp_in = Array(self.endp_ins)
        self.a_endp_out = Array(self.endp_outs)

        #### RX Phy
        self.submodules.usbfsrx = usbfsrx = UsbFsRx(
            usbp_raw = self.iobuf.usb_p_rx,
            usbn_raw = self.iobuf.usb_n_rx
        )

        #### TX Phy
        self.submodules.usbfstx = usbfstx = UsbFsTx(
            i_bit_strobe = usbfsrx.o_bit_strobe,
        )

        self.comb += [
            self.iobuf.usb_tx_en.eq(usbfstx.o_oe),
            self.iobuf.usb_p_tx.eq(usbfstx.o_usbp),
            self.iobuf.usb_n_tx.eq(usbfstx.o_usbn),
        ]

        # Tracks if we are sending Data0 or Data1
        self.last_sent_data = Signal()
        self.last_recv_data = Signal()

        self.submodules.pe = pe = FSM()

        # Waiting for action!
        # --------------------------
        #self.current_pid = Signal(4)
        #self.current_addr = Signal(7)
        #self.current_endp = Signal(4)

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
        self.comb += [
            # Input path
            self.a_endp_out[self.usbfsrx.decode.o_ep].valid.eq(self.usbfsrx.decode.put_data),
            self.a_endp_out[self.usbfsrx.decode.o_ep].data.eq(self.usbfsrx.decode.data_n1),
            # Output path
            self.usbfstx.i_data_valid.eq(self.a_endp_in[self.usbfsrx.decode.o_ep].valid),
            self.usbfstx.i_data_payload.eq(self.a_endp_in[self.usbfsrx.decode.o_ep].data),
            self.a_endp_in[self.usbfsrx.decode.o_ep].ready.eq(self.usbfstx.o_data_get),
        ]

        pe.act("WAIT_TOKEN",
            #If(self.usbfsrx.decode.end_token,
            If(self.usbfsrx.o_pkt_end,
                # Capture addr
                # Capture endpoint
                If(self.usbfsrx.decode.o_pid == PID.SETUP,
                    NextState("RECV_DATA"),
                    NextValue(self.last_sent_data, 0),
                ).Elif(self.usbfsrx.decode.o_pid == PID.OUT,
                    NextState("RECV_DATA"),
                ).Elif(self.usbfsrx.decode.o_pid == PID.IN,
                    # Endpoint isn't ready
                    If(~self.a_endp_in[self.usbfsrx.decode.o_ep].valid,
                        self.usbfstx.i_pid.eq(PID.NAK),
                        self.usbfstx.i_pkt_start.eq(1),
                        NextState("SEND_HAND"),
                    ).Else(
                        If(self.last_sent_data,
                            self.usbfstx.i_pid.eq(PID.DATA0),
                            #NextValue(self.usbfstx.i_pid, PID.DATA0),
                            NextValue(self.last_sent_data, 0),
                        ).Else(
                            self.usbfstx.i_pid.eq(PID.DATA1),
                            #NextValue(self.usbfstx.i_pid, PID.DATA1),
                            NextValue(self.last_sent_data, 1),
                        ),
                        self.usbfstx.i_pkt_start.eq(1),
                        NextState("SEND_DATA"),
                    ),
                ).Else(
                    NextState("ERROR"),
                )
            ),
        )

        # Data packet
        pe.act("RECV_DATA",
            # Was the data path not ready, NAK the packet
            If(self.usbfsrx.decode.put_data & ~(self.a_endp_out[self.usbfsrx.decode.o_ep].ready),
                #NextValue(self.usbfstx.i_pid, PID.NAK),
                #self.usbfstx.i_pid.eq(PID.NAK),
                NextState("WAIT_NAK"),
            ),
            #If(self.usbfsrx.decode.end_data,
            If(self.usbfsrx.o_pkt_end,
                #NextValue(self.usbfstx.i_pid, PID.ACK),
                self.usbfstx.i_pid.eq(PID.ACK),
                self.usbfstx.i_pkt_start.eq(1),
                NextState("SEND_HAND"),
            ),
        )
        pe.act("SEND_DATA",
            If(self.usbfstx.o_pkt_end,
                NextState("RECV_HAND"),
            ),
        )
        # Handshake
        pe.act("RECV_HAND",
            If(self.usbfsrx.o_pkt_end,
                NextState("WAIT_TOKEN"),
            ),
        )
        pe.act("WAIT_NAK",
            If(~self.usbfsrx.decode.pkt_det.o_pkt_active,
                self.usbfstx.i_pid.eq(PID.NAK),
                self.usbfstx.i_pkt_start.eq(1),
                NextState("SEND_HAND"),
            ),
        )
        pe.act("SEND_HAND",
            If(self.usbfstx.o_pkt_end,
                NextState("WAIT_TOKEN"),
            ),
        )
        pe.act("ERROR")


