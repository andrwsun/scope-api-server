# TouchDesigner OSC Out script (run in a DAT with Execute on each frame)
# OSC Out CHOP: oscout2, sending to 127.0.0.1:9000

r = [0.1]
g = [0.3]
b = [0.4]
op('oscout2').sendOSC('/text_r', r)
op('oscout2').sendOSC('/text_g', g)
op('oscout2').sendOSC('/text_b', b)
