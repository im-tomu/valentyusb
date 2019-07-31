# Decoding the Protocol with Sigrok

1. Install sigrok-cli
1. Select `usb_d_n` and `usb_d_p` in gtkwave
1. Press `F4` and give it a name
1. Right click on the new trace and select `Data Format`, then `Transaction Filter Process`
1. Add `dec-usb.sh`
1. Select `dec-usb.sh` from the list
1. Right-click on the new row and say `Add empty row`.  Repeat several times to add more decoders.
