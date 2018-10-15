from migen import *
from migen.genlib.cdc import MultiReg

###############################################################################
###############################################################################
###############################################################################
######
###### Physical Layer Recieve Path
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

        self.specials += MultiReg(usbp_raw, usbp, n=3)
        self.specials += MultiReg(usbn_raw, usbn, n=3)

        
        #######################################################################
        # Line State Recovery State Machine
        #
        # The recieve path doesn't use a differential reciever.  Because of
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
        pkt_end = Signal()

        self.sync += [
            pkt_active.eq(pkt_det.o_pkt_active),
            pkt_end.eq(pkt_det.o_pkt_end)
        ]


        #######################################################################
        #
        # extract PID from packet contents
        #
        self.submodules.pid_shifter = pid_shifter = RxShifter(
            width = 8,
            i_valid = pkt_active & valid,
            i_data = data,
            i_reset = pkt_start
        )

        # check that the PID is consistent
        pid_good = pid_shifter.o_output[0:4] == (pid_shifter.o_output[4:8] ^ 0b1111) 

        # decode packet format type
        pkt_is_handshake = pid_shifter.o_output[0:2] == 0b10
        pkt_is_token = pid_shifter.o_output[0:2] == 0b01
        pkt_is_data = pid_shifter.o_output[0:2] == 0b11


        #######################################################################
        #
        # extract token payload from packet contents
        #
        self.submodules.tok_shifter = tok_shifter = RxShifter(
            width = 16,
            i_valid = pid_shifter.o_full & valid,
            i_data = data,
            i_reset = pkt_start
        )


        #######################################################################
        #
        # check token payload crc5
        #
        self.submodules.tok_crc5 = tok_crc5 = RxCrcChecker(
            width = 5, 
            polynomial = 0b00101, 
            initial = 0b11111, 
            residual = 0b01100, 
            i_valid = pid_shifter.o_full & valid & ~tok_shifter.o_full, 
            i_data = data, 
            i_reset = pkt_start
        ) 


        #######################################################################
        #
        # deserialize data payload from packet contents
        #
        data_put = Signal()

        self.submodules.data_shifter = data_shifter = RxShifter(
            width = 8,
            i_valid = pkt_is_data & pid_shifter.o_full & valid,
            i_data = data,
            i_reset = pkt_start | data_put
        )

        self.comb += [
            data_put.eq(data_shifter.o_full)
        ]


        #######################################################################
        #
        # check data payload crc16
        #
        self.submodules.data_crc16 = data_crc16 = RxCrcChecker(
            width       = 16, 
            polynomial  = 0b1000000000000101, 
            initial     = 0b1111111111111111, 
            residual    = 0b1000000000001101, 
            i_valid     = pid_shifter.o_full & valid & pkt_active, 
            i_data      = data, 
            i_reset     = pkt_start
        ) 


        #######################################################################
        #
        # track bitstuff errors within the packet
        #
        pkt_bitstuff_good = Signal()
        
        # record bitstuff error
        self.sync += [
            If(pkt_start,
                pkt_bitstuff_good.eq(1)
            ).Elif(pkt_active & bitstuff_error,
                pkt_bitstuff_good.eq(0)
            )
        ]


        #######################################################################
        #
        # collect all the packet consistency checks
        #
        self.o_pkt_data_put = Signal()
        crc16_good = Signal(1)

        self.sync += [
            If(self.o_pkt_data_put,
                crc16_good.eq(data_crc16.o_crc_good)    
            )
        ]

        pkt_good = (
            pid_good & 
            pkt_bitstuff_good &
            (tok_crc5.o_crc_good | ~pkt_is_token) &
            (crc16_good | ~pkt_is_data)
        )

        #######################################################################
        #
        # send the output through a pipeline stage
        #
        self.o_pkt_start = Signal()
        self.o_pkt_pid = Signal(4)
        self.o_pkt_token_payload = Signal(11)
        self.o_pkt_data = Signal(8)
        self.o_pkt_good = Signal()
        self.o_pkt_end = Signal()

        self.sync += [
            self.o_pkt_start.eq(pkt_start),
            self.o_pkt_pid.eq(pid_shifter.o_output[0:4]),
            self.o_pkt_token_payload.eq(tok_shifter.o_output[0:11]),
            self.o_pkt_data.eq(data_shifter.o_output[0:8]),
            self.o_pkt_data_put.eq(data_put),
            self.o_pkt_good.eq(pkt_good),
            self.o_pkt_end.eq(pkt_end)
        ]



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
        self.o_pkt_start = decode.o_pkt_start
        self.o_pkt_pid = decode.o_pkt_pid
        self.o_pkt_token_payload = decode.o_pkt_token_payload
        self.o_pkt_data = decode.o_pkt_data
        self.o_pkt_data_put = decode.o_pkt_data_put
        self.o_pkt_good = decode.o_pkt_good
        self.o_pkt_end = decode.o_pkt_end


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

    def __init__(self, i_bit_strobe, i_pkt_start, i_pid, i_token_payload, i_data_valid, i_data_payload):
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

        # the pid shifter shifts out the packet pid and complementary pid.
        # the pid is shifted out when the sync is complete.
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

class UsbOutCpuInterface(Module):
    """
    Implements the SW->HW interface for UsbDevice.
    """
    def __init__(
        self, 
        num_endpoints = 8,

        o_out_ep_ready,

        i_out_start,
        
        i_out_tok_pid,
        i_out_ep_num,
        i_out_data_pid,

        i_out_data,
        i_out_data_put,

        i_out_commit,
        i_out_rollback,
    ):
        self.clock_domains.usb_48 = ClockDomain()

        ### OUT EP Address Space
        # [11] - 0: Register Space, 1: Buffer Space
        #
        # Register Space
        #   [10:7] - Endpoint Number
        #   [6:1]  - Reserved Zero 
        #   [0]    - 1: OUT_EPx_CTRL, 0: OUT_EPx_STAT
        #
        # Buffer Space
        #   [10:7] - Endpoint number
        #   [6]    - Buffer index
        #   [5:0]  - Data buffer byte offset

        ## OUT EP Registers
        #
        # OUT_EPx_STAT
        #   [8]   - Token Type  = 1: SETUP, 0: OUT
        #   [7]   - Transfer Complete
        #   [6:0] - Transfer Length
        #
        # OUT_EPx_CTRL
        #   [7]   - 1: STALL, 0: Normal Operation
        #   [6]   - Expected Data Toggle = 1: DATA1, 0: DATA0
        #   [1]   - Which data buffer does USB device controller own
        #   [0]   - 1: Ready buffer for transfer, 0: Not ready

        ## Buffer Space
        #
        # OUT EP buffers are written by the USB device and read by the host
        # CPU. The host CPU is not able to write to the OUT EP buffers.
        #

        self.specials.buf = buf = Memory(width=32, depth=256)



        #######################################################################
        #######################################################################
        ### System Clock Domain
        #######################################################################
        #######################################################################
        self.bus = bus = wishbone.Interface()
        self.buf_rp = buf.get_port(write_capable=False)
        

        # sys clock domain status registers
        self.transfer_length = Array([Signal(7) for i in range(num_endpoints)])
        self.transfer_complete = Array([Signal(1) for i in range(num_endpoints)])
        self.token_type = Array([Signal(1) for i in range(num_endpoints)])

        # sys clock domain control registers
        self.ready_strobe = Array([Signal(1) for i in range(num_endpoints)])
        self.buffer = Array([Signal(1) for i in range(num_endpoints)])
        self.data_toggle = Array([Signal(1) for i in range(num_endpoints)])
        self.stall = Array([Signal(1) for i in range(num_endpoints)])

        self.sync += [
            self.buf_rp.adr.eq(self.bus.adr[0:8]),

            If(self.bus.adr[],

            )
        ]
        


        #######################################################################
        #######################################################################
        ### USB 48MHz Clock Domain
        #######################################################################
        #######################################################################
        self.buf_wp = buf.get_port(write_capable=True, has_re=True, we_granularity=8, clock_domain="usb_48")

        # usb_48 status registers
        self.usb_48_transfer_length = Array([Signal(7) for i in range(num_endpoints)])
        self.usb_48_transfer_complete = Array([Signal(1) for i in range(num_endpoints)])
        self.usb_48_token_type = Array([Signal(1) for i in range(num_endpoints)])

        # usb_48 control registers
        self.usb_48_ready_strobe = Array([Signal(1) for i in range(num_endpoints)])
        self.usb_48_buffer = Array([Signal(1) for i in range(num_endpoints)])
        self.usb_48_data_toggle = Array([Signal(1) for i in range(num_endpoints)])
        self.usb_48_stall = Array([Signal(1) for i in range(num_endpoints)])
        
        ### endpoint status and control
        buf_put_offset = Signal(7)
        for ep in range(num_endpoints):
            self.sync.usb_48 += [
                If(out_ep_num == ep,
                    # reset the buffer pointer on new packet
                    If(i_out_start,
                        buf_put_offset.eq(0),
                        
                    # reset the buffer pointer on packet replay or when the 
                    # data toggle doesn't match the expected value
                    ).Elif(i_out_rollback | (i_out_commit & (i_out_data_pid[3] != usb_48_data_toggle[ep])),
                        buf_put_offset.eq(0)

                    # update status register on commit
                    ).Elif(i_out_commit,
                        self.usb_48_transfer_length[ep].eq(buf_put_offset),
                        self.usb_48_transfer_complete[ep].eq(1),
                        self.usb_48_token_type[ep].eq(i_out_tok_pid == 0b1101)
                    ),
    
                    # update out buffer write pointer
                    If(i_out_data_put and not self.usb_48_transfer_complete[ep],
                        buf_put_offset.eq(buf_put_offset + 1)
                    ),
                ),

                # software setup a new transfer, clear transfer complete
                If(self.usb_48_ready_strobe[ep],
                    self.usb_48_transfer_complete[ep].eq(0)
                )

                # transfer complete implies software owns the buffer and the 
                # device is not ready for more data from the USB host.
                o_out_ep_ready[ep].eq(~self.usb_48_transfer_complete[ep])
            ]


        ### OUT endpoint data path
        out_we = Signal(1)

        self.sync.usb_48 += [
            out_we.eq(i_out_data_put & ~self.usb_48_transfer_complete[i_out_ep_num] & ~buf_put_offset[6]),

            # packet buffer is 4 bytes wide, but we only get one byte at a
            # time from the protocol engine.
            self.buf_wp.adr.eq(Cat(buf_put_offset[2:6], self.usb_48_buffer, i_out_ep_num)),
            self.buf_wp.dat_w.eq(Cat(i_out_data, i_out_data, i_out_data, i_out_data)),
            self.buf_wp.we(Cat(
                (buf_put_offset[0:2] == 0) & out_we, 
                (buf_put_offset[0:2] == 1) & out_we, 
                (buf_put_offset[0:2] == 2) & out_we, 
                (buf_put_offset[0:2] == 3) & out_we)),
        ]




class UsbDevice(Module):
    def __init__(self, usbp, usbn, dev_addr):
        self.dev_addr = dev_addr


        #######################################################################
        #######################################################################
        #### out ep interface
        #######################################################################
        #######################################################################
        # 
        self.o_out_ep_num = Signal(4)
        self.o_out_data_pid = Signal(4)
        self.i_out_ep_ready = Array([Signal() for i in range(16)])
        self.o_out_start = Signal()
        self.o_out_commit = Signal()
        self.o_out_rollback = Signal()
        self.o_out_data = Signal(8)
        self.o_out_data_put = Signal()
        
        #######################################################################
        #######################################################################
        #### in ep interface
        #######################################################################
        #######################################################################
        #
        self.o_in_ep_num = Signal(4)
        self.o_in_ep_data_pid = Array([Signal() for i in range(16)])
        self.i_in_ep_ready = Array([Signal() for i in range(16)])
        self.o_in_start = Signal()
        self.o_in_commit = Signal()
        self.o_in_rollback = Signal()
        self.i_in_data = Signal(8)
        self.i_in_data_valid = Signal()
        self.o_in_data_get = Signal()
       



        ## protocol engine -> tx mux
        self.in_tx_pkt_start = Signal()
        self.in_tx_pid = Signal(4)

        self.out_tx_pkt_start = Signal()
        self.out_tx_pid = Signal(4)


                         

        # usb_tx interface
        self.tx_pkt_start   = Signal(1)
        self.tx_pkt_end     = Signal(1)
        self.tx_pid         = Signal(4)
        self.tx_data_avail  = Signal(1)
        self.tx_data_get    = Signal(1)
        self.tx_data        = Signal(8) # FIXME: this needs to be synchronized with tx_data_avail/get


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

                io_PACKAGE_PIN = usbp,
                i_OUTPUT_ENABLE = self.usb_tx_en,
                i_D_OUT_0 = self.usb_p_tx,
                o_D_IN_0 = self.usb_p_rx_io
            ),

            Instance(
                "SB_IO",
                p_PIN_TYPE = Raw("6'b101001"),
                p_PULLUP = 0b0,

                io_PACKAGE_PIN = usbn,
                i_OUTPUT_ENABLE = self.usb_tx_en,
                i_D_OUT_0 = self.usb_n_tx,
                o_D_IN_0 = self.usb_n_rx_io
            )
        ]

        
        #######################################################################
        #######################################################################
        #### RX Phy
        #######################################################################
        #######################################################################
        self.submodules.usbfsrx = usbfsrx = UsbFsRx(
            usbp_raw = self.usb_p_rx,
            usbn_raw = self.usb_n_rx
        )
        
        ## usb_rx interface
        self.rx_pkt_start   = usbfsrx.o_pkt_start
        self.rx_pkt_end     = usbfsrx.o_pkt_end
        self.rx_pid         = usbfsrx.o_pkt_pid
        self.rx_addr        = Signal(7)
        self.rx_endp        = Signal(4)
        self.rx_frame_num   = usbfsrx.o_pkt_token_payload
        self.rx_data_put    = usbfsrx.o_pkt_data_put
        self.rx_data        = usbfsrx.o_pkt_data
        self.rx_pkt_valid   = usbfsrx.o_pkt_good

        self.comb += [
            self.rx_addr.eq(usbfsrx.o_pkt_token_payload[4:11]),
            self.rx_endp.eq(usbfsrx.o_pkt_token_payload[0:4])
        ]
       

        #######################################################################
        #######################################################################
        #### TX Phy
        #######################################################################
        #######################################################################
        self.submodules.usbfstx = usbfstx = UsbFsTx(
            i_bit_strobe = usbfsrx.o_bit_strobe,
            i_pkt_start = self.tx_pkt_start,
            i_pid = self.tx_pid,
            i_token_payload = Constant(0, 11),
            i_data_valid = self.tx_data_avail,
            i_data_payload = self.tx_data,
        )

        self.comb += [
            self.usb_tx_en.eq(usbfstx.o_oe),
            self.usb_p_tx.eq(usbfstx.o_usbp),
            self.usb_n_tx.eq(usbfstx.o_usbn),
            self.tx_pkt_end.eq(usbfstx.o_pkt_end),
            self.tx_data_get.eq(usbfstx.o_data_get)
        ]


        
        #######################################################################
        #######################################################################
        #### Protocol Engine to TX Phy Mux
        #######################################################################
        #######################################################################
        self.sync += [
            self.tx_pkt_start.eq(self.in_tx_pkt_start | self.out_tx_pkt_start),
            self.tx_pid.eq(Mux(self.out_tx_pkt_start, self.out_tx_pid, self.in_tx_pid)),
        ]


        #######################################################################
        #######################################################################
        #### Protocol Engine Support
        #######################################################################
        #######################################################################
        #
        # Protocol Engine Support is responsible for basic functionality 
        # required by both the OUT and IN protocol engines.  This includes
        # keeping track of the current transfer address, endpoint number,
        # and start of frame tokens.
        #
        
        # USB Packet IDs
        PID_OUT     = 0b0001
        PID_IN      = 0b1001
        PID_SOF     = 0b0101
        PID_SETUP   = 0b1101
        PID_DATA0   = 0b0011
        PID_DATA1   = 0b1011
        PID_ACK     = 0b0010
        PID_NAK     = 0b1010
        PID_STALL   = 0b1110

        # PID from most recent token
        self.current_token = Signal(4)

        # Endpoint number from most recent token
        self.current_endp = Signal(4)

        # True if most recent packet received was a valid token directed 
        # towards this usb device
        self.valid_request_token_pre = Signal(1)
        self.valid_request_token = Signal(1)

        self.comb += [
            self.valid_request_token_pre.eq(
                (self.rx_pkt_valid == 0b1) and
                (self.rx_pid[0:2] == 0b01) and
                (self.rx_addr == self.dev_addr)
            )    
        ]

        self.sync += [
            self.valid_request_token.eq(self.valid_request_token_pre),

            If(self.valid_request_token,
                self.current_token.eq(self.rx_pid),
                self.current_endp.eq(self.rx_endp)
            )
        ]

        #######################################################################
        #######################################################################
        #### OUT Protocol Engine
        #######################################################################
        #######################################################################
        # 
        # The OUT Protocol Engine handles data transfer OUT from the USB host
        # to the USB device. It is responsible for acknowledging valid data
        # from the host by sending ACK handshake packets, applying back-pressure
        # with NAK handshake packets, and rejecting bad data packets by not
        # responding.
        #
        # The OUT PE explicitly does not check data toggle.  It is up to the 
        # endpoint buffer to track, handle, and reset data toggle.
        #

        # Qualify with rx_pkt_end to indicate a valid out token has 
        # been received.
        self.valid_out_token = Signal(1)

        self.comb += [
            self.valid_out_token.eq(
                self.valid_request_token &
                (
                    self.current_token == PID_OUT or
                    self.current_token == PID_SETUP
                )
            )
        ]

        # Qualify with rx_pkt_end to indicate a valid data packet has been
        # received
        self.valid_data_packet = Signal(1)

        self.comb += [
            self.valid_data_packet.eq(
                self.rx_pkt_valid == 0b1 and
                self.rx_pid[0:3] == 0b011
            )       
        ]


        ###############################
        ## OUT State Machine
        ###############################
        self.submodules.out_pe = out_pe = FSM()

        # Wait for a valid OUT or SETUP token.  Once we get a token, decide
        # immediately whether any coming data needs to be NAKed or not.
        out_pe.act(
            "WAIT_OUT_TOK",
            If(self.rx_pkt_end & self.valid_out_token, 
                If(self.out_ep_ready[self.current_endp], 
                    NextState("WAIT_DATA")
                ).Else(
                    NextState("WAIT_DATA_NAK")
                )
            )
        )

        # Wait for a valid data packet.  If any other packet comes in, or if the
        # data packet is not valid, or if the data PID does not match, then cancel
        # and roll-back the current data transfer.
        out_pe.act(
            "WAIT_DATA",
            If(self.rx_pkt_end,
                If(self.valid_data_packet,
                    NextState("SEND_ACK")
                    
                ).Else(
                    NextState("ROLLBACK")
                ),
                
                NextState("WAIT_OUT_TOK")
            )
        )

        out_pe.act(
            "SEND_ACK",
            self.out_tx_pid.eq(PID_ACK), 
            self.out_tx_pkt_start.eq(1),
            self.o_out_commit.eq(1), 
            NextState("WAIT_OUT_TOK")
        )

        # Similar to the WAIT_DATA state, except this will always roll-back the transfer
        # and reply to a valid data packet with with a NAK handshake.
        out_pe.act(
            "WAIT_DATA_NAK",
            If(self.rx_pkt_end,
                If(self.valid_data_packet,
                    NextState("SEND_NAK")
                ).Else(
                    NextState("ROLLBACK")    
                )
            )
        )

        out_pe.act(
            "SEND_NAK",
            self.out_tx_pid.eq(PID_NAK), 
            self.out_tx_pkt_start.eq(1),
            NextState("ROLLBACK")
        )

        out_pe.act(
            "ROLLBACK",
            self.out_rollback.eq(1),
            NextState("WAIT_OUT_TOK")
        )

        self.sync += [
            self.o_out_start.eq(out_start),

            If(out_start,
                self.o_out_ep_num.eq(self.current_endp),
                self.o_out_data_pid.eq()
            )
        ]

        #######################################################################
        #######################################################################
        #### IN Protocol Engine
        #######################################################################
        #######################################################################
        # 
        # The IN Protocol Engine handles data transfer IN to the USB host
        # from the USB device. It is responsible for sending data packets to the
        # host in response to IN tokens, applying back-pressure with NAK 
        # handshake packets, and replaying data packets that were not yet
        # acknowledged by the host
        #
        # The IN PE explicitly does not generate its own data toggle.  It is up
        # to the endpoint buffer to set the data toggle to the IN PE.
        #

        # Qualify with rx_pkt_end to indicate a valid in token has 
        # been received.
        self.valid_in_token = Signal(1)

        self.comb += [
            self.valid_in_token.eq(
                self.valid_request_token &
                self.current_token == PID_IN
            )
        ]

        # Qualify with rx_pkt_end to indicate a valid ACK handshake has
        # been received
        self.valid_ack_packet = Signal(1)

        self.comb += [
            self.valid_ack_packet.eq(
                self.rx_pkt_valid == 0b1 and
                self.rx_pid == PID_ACK
            )       
        ]


        ###############################
        ## IN State Machine
        ###############################
        self.submodules.in_pe = in_pe = FSM()

        # Wait for a valid IN token then move on to the next state.
        in_pe.act(
            "WAIT_IN_TOK",
            If(self.rx_pkt_end & self.valid_in_token, 
                NextState("SEND_RESPONSE")
            )
        )

        # Decide whether a data packet or NAK handshake should be sent.
        in_pe.act(
            "SEND_RESPONSE",
            If(self.in_ep_ready[self.current_endp], 
                NextState("SEND_DATA")
            ).Else(
                NextState("SEND_NAK")
            )
        )

        # Send a data packet to the host
        in_pe.act(
            "SEND_DATA",
            self.in_tx_pid.eq(Mux(self.in_ep_data_pid[self.current_endp], PID_DATA1, PID_DATA0)), 
            self.in_tx_pkt_start.eq(1),
            NextState("WAIT_ACK")
        )

        # Send a NAK handshake to the host
        in_pe.act(
            "SEND_NAK",
            self.in_tx_pid.eq(PID_NAK),
            self.in_tx_pkt_start.eq(1),
            NextState("WAIT_IN_TOK")
        )

        # Wait for ACK handshake from the host. If a valid packet other than ACK
        # is received, then the transfer failed and must be rolled back.  If a
        # valid IN token is recieved, then move to the SEND_RESPONSE state.
        in_pe.act(
            "WAIT_ACK",
            If(self.rx_pkt_end,
                If(self.valid_ack_packet,
                    self.in_commit.eq(1),
                    NextState("WAIT_IN_TOK")
                ).Elif(self.valid_in_token,
                    self.in_rollback.eq(1),
                    NextState("SEND_RESPONSE")
                ).Else(
                    self.in_rollback.eq(1),
                    NextState("WAIT_IN_TOK")
                )
            )
        )
        
