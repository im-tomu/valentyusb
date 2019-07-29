#include <stddef.h>
#include <stdint.h>
#include <generated/csr.h>
#include <irq.h>
#include <string.h>

// #ifdef CSR_USB_EP_0_OUT_EV_PENDING_ADDR
#if 1

struct usb_setup_request {
    union {
        struct {
            uint8_t bmRequestType;
            uint8_t bRequest;
        };
        uint16_t wRequestAndType;
    };
    uint16_t wValue;
    uint16_t wIndex;
    uint16_t wLength;
};

static const int max_byte_length = 64;

static const uint8_t * volatile current_data;
static volatile int current_length;
static volatile int data_offset;
static volatile int data_to_send;
static int next_packet_is_empty;

static const uint8_t usb_device_descriptor[] = {
    0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
    0x09, 0x12, 0xf0, 0x5b, 0x01, 0x01, 0x01, 0x02,
    0x00, 0x01
};

static const uint8_t usb_config_descriptor[] = {
    0x09, 0x02, 0x12, 0x00, 0x01, 0x01, 0x01, 0x80,
    0x32, 0x09, 0x04, 0x00, 0x00, 0x00, 0xfe, 0x00,
    0x00, 0x02
};
        
static const uint8_t usb_string0_descriptor[] = {
    0x04, 0x03, 0x09, 0x04,
};

static const uint8_t usb_string1_descriptor[] = {
    0x0e, 0x03, 0x46, 0x00, 0x6f, 0x00, 0x6f, 0x00,
    0x73, 0x00, 0x6e, 0x00, 0x00, 0x00,
};

static const uint8_t usb_string2_descriptor[] = {
    0x1a, 0x03, 0x46, 0x00, 0x6f, 0x00, 0x6d, 0x00,
    0x75, 0x00, 0x20, 0x00, 0x55, 0x00, 0x70, 0x00,
    0x64, 0x00, 0x61, 0x00, 0x74, 0x00, 0x65, 0x00,
    0x72, 0x00,
};

static const uint8_t usb_bos_descriptor[] = {
    0x05, 0x0f, 0x1d, 0x00, 0x01, 0x18, 0x10, 0x05,
    0x00, 0x38, 0xb6, 0x08, 0x34, 0xa9, 0x09, 0xa0,
    0x47, 0x8b, 0xfd, 0xa0, 0x76, 0x88, 0x15, 0xb6,
    0x65, 0x00, 0x01, 0x02, 0x01,
};

static const uint8_t usb_ms_compat_id_descriptor[] = {
    0x28, 0x00, 0x00, 0x00, 0x00, 0x01, 0x04, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x01, 0x57, 0x49, 0x4e, 0x55, 0x53, 0x42,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};


#define MSFT_VENDOR_CODE    '~'     // Arbitrary, but should be printable ASCII
static const uint8_t usb_string_microsoft[18] = {
    18, 3, 'M','S','F','T','1','0','0', MSFT_VENDOR_CODE,
    0, 0, 0, 0, 0, 0, 0, 0,
};


static uint8_t reply_buffer[8];
static uint8_t usb_configuration = 0;

// Note that our PIDs are only bits 2 and 3 of the token,
// since all other bits are effectively redundant at this point.
enum USB_PID {
    USB_PID_OUT   = 0,
    USB_PID_SOF   = 1,
    USB_PID_IN    = 2,
    USB_PID_SETUP = 3,
};

enum epfifo_response {
    EPF_ACK = 0,
    EPF_NAK = 1,
    EPF_NONE = 2,
    EPF_STALL = 3,
};

#define USB_EV_ERROR 1
#define USB_EV_PACKET 2

void usb_idle(void);
void usb_disconnect(void);
void usb_connect(void);

static void usb_setup(const struct usb_setup_request *setup, uint32_t size);

void usb_idle(void) {
    usb_ep_0_out_ev_enable_write(0);
    usb_ep_0_in_ev_enable_write(0);

    // Reject all incoming data, since there is no handler anymore
    usb_ep_0_out_respond_write(EPF_NAK);

    // Reject outgoing data, since we don't have any to give.
    usb_ep_0_in_respond_write(EPF_NAK);

    irq_setmask(irq_getmask() & ~(1 << USB_INTERRUPT));
}

void usb_disconnect(void) {
    usb_ep_0_out_ev_enable_write(0);
    usb_ep_0_in_ev_enable_write(0);
    irq_setmask(irq_getmask() & ~(1 << USB_INTERRUPT));
    usb_pullup_out_write(0);
}

void usb_connect(void) {

    usb_ep_0_out_ev_pending_write(usb_ep_0_out_ev_enable_read());
    usb_ep_0_in_ev_pending_write(usb_ep_0_in_ev_pending_read());
    usb_ep_0_out_ev_enable_write(USB_EV_PACKET | USB_EV_ERROR);
    usb_ep_0_in_ev_enable_write(USB_EV_PACKET | USB_EV_ERROR);

    // Accept incoming data by default.
    usb_ep_0_out_respond_write(EPF_ACK);

    // Reject outgoing data, since we have none to give yet.
    usb_ep_0_in_respond_write(EPF_NAK);

    usb_pullup_out_write(1);

	irq_setmask(irq_getmask() | (1 << USB_INTERRUPT));
}

void usb_init(void) {
    usb_pullup_out_write(0);
}

static void process_tx(void) {

    // Don't allow requeueing -- only queue more data if we're
    // currently set up to respond NAK.
    if (usb_ep_0_in_respond_read() != EPF_NAK) {
        return;
    }

    // Prevent us from double-filling the buffer.
    if (!usb_ep_0_in_ibuf_empty_read()) {
        return;
    }

    if (!current_data || !current_length) {
        return;
    }

    data_offset += data_to_send;

    data_to_send = current_length - data_offset;

    // Clamp the data to the maximum packet length
    if (data_to_send > max_byte_length) {
        data_to_send = max_byte_length;
        next_packet_is_empty = 0;
    }
    else if (data_to_send == max_byte_length) {
        next_packet_is_empty = 1;
    }
    else if (next_packet_is_empty) {
        next_packet_is_empty = 0;
        data_to_send = 0;
    }
    else if (current_data == NULL || data_to_send <= 0) {
        next_packet_is_empty = 0;
        current_data = NULL;
        current_length = 0;
        data_offset = 0;
        data_to_send = 0;
        return;
    }

    int this_offset;
    for (this_offset = data_offset; this_offset < (data_offset + data_to_send); this_offset++) {
        usb_ep_0_in_ibuf_head_write(current_data[this_offset]);
    }
    usb_ep_0_in_respond_write(EPF_ACK);
    return;
}

static void usb_send(const void *data, int total_count) {

    while ((current_length || current_data))// && usb_ep_0_in_respond_read() != EPF_NAK)
        ;
    current_data = (uint8_t *)data;
    current_length = total_count;
    data_offset = 0;
    data_to_send = 0;
    process_tx();
}

void usb_isr(void) {
    uint8_t ep0o_pending = usb_ep_0_out_ev_pending_read();
    uint8_t ep0i_pending = usb_ep_0_in_ev_pending_read();

    // We got an OUT or a SETUP packet.  Handle it.
    if (ep0o_pending) {
        uint8_t last_tok = usb_ep_0_out_last_tok_read();
        uint32_t obuf_len = 0;
        static uint8_t obuf[128];
        if (!usb_ep_0_out_obuf_empty_read()) {
            while (!usb_ep_0_out_obuf_empty_read()) {
                obuf[obuf_len++] = usb_ep_0_out_obuf_head_read();
                usb_ep_0_out_obuf_head_write(0);
            }
        }

        if (obuf_len >= 2)
            obuf_len -= 2 /* Strip off CRC16 */;

        if (last_tok == USB_PID_SETUP) {
            usb_ep_0_in_dtb_write(1);
            data_offset = 0;
            current_length = 0;
            current_data = NULL;
            usb_setup((const void *)obuf, obuf_len);
        }

        usb_ep_0_out_ev_pending_write(ep0o_pending);
        usb_ep_0_out_respond_write(EPF_ACK);
    }

    // We just got an "IN" token.  Send data if we have it.
    if (ep0i_pending) {
        usb_ep_0_in_respond_write(EPF_NAK);
        usb_ep_0_in_ev_pending_write(ep0i_pending);
    }
    
    return;
}

static void usb_ack_in(void) {
    while (usb_ep_0_in_respond_read() == EPF_ACK)
        ;
    usb_ep_0_in_respond_write(EPF_ACK);
}

static void usb_err(void) {
    usb_ep_0_out_respond_write(EPF_STALL);
    usb_ep_0_in_respond_write(EPF_STALL);
}


static void usb_setup(const struct usb_setup_request *setup, uint32_t size)
{
    const uint8_t *data = NULL;
    uint32_t datalen = 0;
    (void)size;

    switch (setup->wRequestAndType)
    {
    case 0x0500: // SET_ADDRESS
    case 0x0b01: // SET_INTERFACE
        break;

    case 0x0900: // SET_CONFIGURATION
        usb_configuration = setup->wValue;
        break;

    case 0x0880: // GET_CONFIGURATION
        reply_buffer[0] = usb_configuration;
        datalen = 1;
        data = reply_buffer;
        break;

    case 0x0080: // GET_STATUS (device)
        reply_buffer[0] = 0;
        reply_buffer[1] = 0;
        datalen = 2;
        data = reply_buffer;
        break;

    case 0x0082: // GET_STATUS (endpoint)
        if (setup->wIndex > 0)
        {
            usb_err();
            return;
        }
        reply_buffer[0] = 0;
        reply_buffer[1] = 0;
        data = reply_buffer;
        datalen = 2;
        break;

    case 0x0102: // CLEAR_FEATURE (endpoint)
        if (setup->wIndex > 0 || setup->wValue != 0)
        {
            // TODO: do we need to handle IN vs OUT here?
            usb_err();
            return;
        }
        break;

    case 0x0302: // SET_FEATURE (endpoint)
        if (setup->wIndex > 0 || setup->wValue != 0)
        {
            // TODO: do we need to handle IN vs OUT here?
            usb_err();
            return;
        }
        break;

    case 0x0680: // GET_DESCRIPTOR
    case 0x0681:
        #define CASE_VALUE(match, result) case match: data = result; datalen = sizeof(result); break
        switch (setup->wValue) {
            CASE_VALUE(0x0100, usb_device_descriptor);
            CASE_VALUE(0x0200, usb_config_descriptor);
            CASE_VALUE(0x0300, usb_string0_descriptor);
            CASE_VALUE(0x0301, usb_string1_descriptor);
            CASE_VALUE(0x0302, usb_string2_descriptor);
            CASE_VALUE(0x03ee, usb_string_microsoft);
            CASE_VALUE(0x0f00, usb_bos_descriptor);
            default: usb_err(); return;
        }
        #undef CASE_VALUE
        goto send;

    case (MSFT_VENDOR_CODE << 8) | 0xC0: // Get Microsoft descriptor
    case (MSFT_VENDOR_CODE << 8) | 0xC1:
        if (setup->wIndex == 0x0004)
        {
            // Return WCID descriptor
            data = usb_ms_compat_id_descriptor;
            datalen = sizeof(usb_ms_compat_id_descriptor);
            break;
        }
        usb_err();
        return;

#ifdef LANDING_PAGE_URL
    case (WEBUSB_VENDOR_CODE << 8) | 0xC0: // Get WebUSB descriptor
        if (setup->wIndex == 0x0002)
        {
            if (setup->wValue == 0x0001)
            {
                data = get_landing_url_descriptor(&datalen);
                break;
            }
        }
        usb_err();
        return;
#endif

    default:
        usb_err();
        return;
    }

send:
    if (data && datalen) {
        if (datalen > setup->wLength)
            datalen = setup->wLength;
        usb_send(data, datalen);
    }
    else
        usb_ack_in();
    return;
}
