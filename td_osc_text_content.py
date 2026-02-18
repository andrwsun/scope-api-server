# TouchDesigner OSC Out script — text content
# Run in a DAT Execute, or trigger on a string change / button press
# OSC Out CHOP: oscout2, sending to 127.0.0.1:9000
#
# Parameter: /text  (string)
# Sets the text displayed on screen.
# Overrides whatever is typed in the browser UI prompt box.

op('oscout2').sendOSC('/text', ['Hello World'])
