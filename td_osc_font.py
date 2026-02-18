# TouchDesigner OSC Out script — font selection
# Run in a DAT Execute, or trigger on button press / string switch
# OSC Out CHOP: oscout2, sending to 127.0.0.1:9000
#
# Parameter: /font_name  (string)
# Only fonts installed on the server machine are accepted.
# Sending an unlisted name falls back to system default.
#
# Available values (macOS):
#   'Helvetica'       — system sans-serif
#   'Arial'           — requires Office fonts installed
#   'Times New Roman' — requires Office fonts installed
#   'Courier'         — monospace
#   'Verdana'         — requires Office fonts installed
#   'Georgia'         — requires Office fonts installed
#   'Monaco'          — monospace, macOS only
#   'SF Pro Display'  — macOS system UI font
#   'SF Mono'         — macOS monospace system font
#   'Comic Sans MS'   — requires Office fonts installed
#   'Impact'          — requires Office fonts installed
#   'Trebuchet MS'    — requires Office fonts installed

font = ['Helvetica']
op('oscout2').sendOSC('/font_name', font)
