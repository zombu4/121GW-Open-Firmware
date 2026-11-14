"""Induced emulator test of the current clamp mode (production image, DESIGN.md 10 IMP-68, FEATURES.md 2.41; GitHub issue 39).

    python -I emulator/scripts/test_clamp.py [--cases a,b,...] [image]      default every case, firmware/build/EEVBlog.bin

Cases (each boots its own emulator): matrix (T1), hold_rel (T2), other_modes (T3), page (T4), csv (T5), rel_beyond (T6),
byte13_gates (T7, T9), lamp_test (T8).  CI runs two jobs of this script at the same time, one per case group
(.github/scripts/plan_emulator_tests.py): all of them in one job ran past the 2280 s limit on a hosted runner (T1 11 min,
T4 15 min, T5 7 min there).

SETUP page CLP (item 48, DCV, ACV, DCmV and ACmV, after dSP), EEPROM 0x020 bits 7..5 = the ratio code 0..4 (0 off, 1..4 =
1, 10, 100, 1000 mV per A).  With a ratio the main display shows the current of a clamp with a voltage output: the counts
of the voltage reading with the point moved, in A, in mA (above 4 places) or " OFL " (below 0 places); the unit signs V and
the m of DCmV / ACmV are replaced by A (and m for mA).  What is shown is compared with the current computed from the
voltage reading D (0x20007D64), the places of the range (0x2000803F) and the mode, by the physics (amperes = volts / ratio),
not by the firmware's formula.
  T1 the display matrix: DCV, ACV, DCmV and ACmV at two ranges each, the codes 1..4 and then off again (the V sign and the m
     of the mV modes come back): digits, point, unit signs; the BLE status frame byte 13 bits 7..5 = the code, bits 4..0
     unchanged by it (frames from USART2); the cases d' > 4 (mA) of DCmV and d' < 0 (" OFL ") of DCV occur
  T2 HOLD keeps the amperes; REL shows the difference in amperes; REL in percent shows percent, no A
  T3 other modes unaffected: DCmA with a ratio set: the current as always, byte 13 bits 7..5 = 0
  T4 the page CLP: from dSP in DCV, ACV, DCmV, ACmV; "CLP" on the main display, the ratio on the sub display; long SETUP,
     REL x3 saves exactly EEPROM 0x020 bits 7..5 (the other bits kept); the wrap up / down; the setting after a power cycle;
     an edit left unsaved is undone by the edit timeout; the rEL page keeps bits 7..5; codes 5..7 and an erased byte read as off
  T5 the log file: unit A and the value with d' places (DCV), mA (DCmV), and the counts followed by -d' zeros (d' < 0), also
     in the closing MAX and MIN rows
  T6 REL difference beyond 99999 (DCmV range 0 at 100 mV per A, REL on -53 mV then +53 mV and the mirror): D / 10 is drawn with
     one place fewer; the digits, the point and the unit signs agree (A, point at 4 places, m off)
  T7 byte 13 bits 7..5 are the code only while the display shows the clamp view: 0 in calibration mode, in the MEM view, with
     REL shown as percent, on the page CLP and on the pages Sd, tHI and tLo; the code again when each is left
  T8 the lamp test of the power-on window (RANGE + MIN/MAX held at power-on) lights every segment with a ratio set, in DCV
     and DCmV, as without one (ui_relpct_mask applies no view while ui_lamp_test is on)
  T9 REL in percent left with a ratio: the % sign goes out and the A sign is on (the change from the percent view to the clamp
     view requests the annunciator redraw)
RUN LIMIT: 1 run of this script.
The helpers are those of test_backlight_timer.py and test_log_trigger.py.
"""
import math
import os
import re
import struct
import sys

sys.dont_write_bytecode = True                       # keep the tree free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_backlight_timer as T  # noqa: E402
import test_log_trigger as L  # noqa: E402
from emu.fat32 import Volume  # noqa: E402

ADDR_MODE, ADDR_RANGE, ADDR_DP = 0x2000803C, 0x2000803D, 0x2000803F
ADDR_D = 0x20007D64                                  # displayed reading D (counts, after REL)
ADDR_ITEM, ADDR_EDIT = 0x20008037, 0x2000806E
ADDR_MM_STATE, ADDR_REL = 0x2000804E, 0x20008058   # minmax_state, rel_active
EE_MV_MEMORY = 0x009                                 # the function of the mV position: 3 DCmV, 4 ACmV
ITEM_DSP, ITEM_CLP, ITEM_LT, ITEM_REL = 24, 48, 27, 22
SYMS = {}


def s32(e, addr):
    return struct.unpack('<i', e.m.uc.mem_read(addr, 4))[0]


def setcode(e, code):
    """Put the ratio into RAM (the display, the frame and the log file read set_clamp_code)."""
    e.m.uc.mem_write(SYMS['set_clamp_code'], bytes([code]))


def look(e):
    """(main text without blanks, V, A, m): the main display and the unit signs of the LCD words."""
    w = e.snapshot()['lcd']
    main = T.DEC.text(w)[0].replace(' ', '')
    return main, w[4] >> 10 & 1, w[6] >> 10 & 1, w[2] >> 10 & 1


def expected(mode, d, counts, code):
    """(unit, value, places): 'V' (or mV), 'A', 'mA' or 'OFL'.  Amperes = volts / ratio, the digits are the counts."""
    volts = counts * 10.0 ** -d * (1.0 if mode in (1, 2) else 1e-3)
    if code == 0:
        return 'V', counts * 10.0 ** -d, d
    amps = volts / 10.0 ** (code - 4)                # 1, 10, 100, 1000 mV per A = 0.001 .. 1 V per A
    places = round(math.log10(counts / amps))        # digits after the point when the counts read as amperes
    if places < 0:
        return 'OFL', None, 0
    if places <= 4:
        return 'A', amps, places
    return 'mA', amps * 1000.0, places - 3


def check_view(e, name, mode, code, want_places=None):
    """Compare the display with expected(): returns (unit, places) that were expected."""
    counts, d = abs(s32(e, ADDR_D)), T.rd(e, ADDR_DP)
    if counts > 99999:                               # a REL difference beyond five digits: D / 10 with one place fewer (the
        counts, d = counts // 10, max(d - 1, 0)      # same volts), the digits, the point and the unit all follow that place
    unit, value, places = expected(mode, d, counts, code)
    main, v, a, m = look(e)
    detail = 'main %r V%d A%d m%d  counts %d d %d -> %s' % (main, v, a, m, counts, d, unit)
    if unit == 'OFL':
        ok = main.upper().replace('0', 'O') == 'OFL' and (v, a, m) == (0, 1, 0)
    else:
        try:
            shown = float(main)
        except ValueError:
            shown = None
        got_places = len(main) - main.index('.') - 1 if '.' in main else 0
        signs = {'V': (1, 0, int(mode in (3, 4))), 'A': (0, 1, 0), 'mA': (0, 1, 1)}[unit]
        ok = (shown is not None and abs(abs(shown) - value) <= 0.03 * value + 2.0 * 10.0 ** -places
              and got_places == places and (v, a, m) == signs)
    T.check(name, ok, detail)
    return unit, places


def frame_byte13(e, seconds):
    """Advance and return byte 13 of the last complete, checksum-valid status frame of those seconds (None if none)."""
    n0 = len(e.ble_rx)
    e.advance(seconds)
    data, i, last = bytes(e.ble_rx[n0:]), 0, None
    while i + 19 <= len(data):
        if data[i] == 0xF2:
            x = 0
            for b in data[i:i + 19]:
                x ^= b
            if x == 0:
                last = data[i:i + 19]
                i += 19
                continue
        i += 1
    return None if last is None else last[13]


# (name, dial, EEPROM patch, key to press after the dial, mode, stimuli: (volts, ac)) -- two ranges per mode
MATRIX = [
    ('DCV', 2, {}, None, 1, [(0.2534, False), (123.4, False)]),
    ('ACV', 2, {}, 'MODE', 2, [(1.2, True), (12.0, True)]),
    ('DCmV', 3, {EE_MV_MEMORY: 3}, None, 3, [(0.0200, False), (0.2000, False)]),
    ('ACmV', 3, {EE_MV_MEMORY: 4}, None, 4, [(0.0200, True), (0.2000, True)]),
]


def matrix(image):
    print('T1 the display matrix')
    seen = set()
    for name, dial, patch, key, mode, stimuli in MATRIX:
        e = T.make(image, patch)
        e.command({'dial': dial})
        e.advance(3.0)
        if key:
            T.press(e, key, gap=1.0)
        T.long_press(e, 'PEAK')                      # Bluetooth on: the status frames
        for volts, ac in stimuli:
            e.command({'input': dict({'kind': 'V', 'value': volts, 'ac': ac}, **({'freq': 1000} if ac else {}))})
            e.advance(4.0)
            T.check('%s %g V: mode %d' % (name, volts, mode), T.rd(e, ADDR_MODE) == mode and abs(s32(e, ADDR_D)) >= 1000,
                    'mode %d counts %d range %d' % (T.rd(e, ADDR_MODE), s32(e, ADDR_D), T.rd(e, ADDR_RANGE)))
            setcode(e, 0)
            base13 = frame_byte13(e, 1.3)
            for code in (1, 2, 3, 4, 0):
                setcode(e, code)
                b13 = frame_byte13(e, 1.3)
                unit, places = check_view(e, '%s %g V, ratio code %d' % (name, volts, code), mode, code)
                seen.add((mode, unit))
                T.check('%s %g V code %d: frame byte 13 bits 7..5 = %d, bits 4..0 as without a ratio' % (name, volts, code, code),
                        b13 is not None and base13 is not None and b13 >> 5 == code and (b13 & 0x1F) == (base13 & 0x1F),
                        'byte 13 %r, without %r' % (b13, base13))
        s = e.snapshot()
        T.check('%s: no unmodelled access, no safety violation' % name, s['unmodelled'] == 0 and not s['safety'])
    T.check('the cases mA (DCmV or ACmV) and OFL (DCV) occurred', ('mA' in {u for m, u in seen if m in (3, 4)})
            and 'OFL' in {u for m, u in seen if m == 1}, repr(sorted(seen)))


def hold_rel(image):
    print('T2 HOLD, REL, REL in percent')
    e = T.make(image, {0x20: 0x40})                  # ratio code 2: 10 mV per A
    e.command({'dial': 2})
    e.advance(3.0)
    e.command({'input': {'kind': 'V', 'value': 1.2, 'ac': False}})
    e.advance(4.0)
    check_view(e, 'DCV 1.2 V at 10 mV/A', 1, 2)
    T.press(e, 'HOLD', gap=1.0)
    held = look(e)
    e.command({'input': {'kind': 'V', 'value': 2.0, 'ac': False}})
    e.advance(3.0)
    T.check('HOLD: the held 120 A stay (A sign on, V off)', look(e) == held and abs(float(held[0]) - 120.0) < 3.0
            and held[1:] == (0, 1, 0), 'held %r now %r' % (held, look(e)))
    T.press(e, 'HOLD', gap=1.0)
    e.advance(3.0)
    check_view(e, 'HOLD off: 2.0 V = 200 A again', 1, 2)
    e = T.make(image, {0x20: 0x40})
    e.command({'dial': 2})
    e.advance(3.0)
    e.command({'input': {'kind': 'V', 'value': 1.0, 'ac': False}})
    e.advance(4.0)
    T.press(e, 'REL', gap=1.0)
    e.command({'input': {'kind': 'V', 'value': 1.5, 'ac': False}})
    e.advance(4.0)
    counts = s32(e, ADDR_D)
    T.check('REL on 1.0 V, then 1.5 V: D is the difference, about 5000 counts', 4000 <= counts <= 6000, 'D %d' % counts)
    check_view(e, 'REL: the difference 0.5 V at 10 mV/A = 50 A', 1, 2)
    e = T.make(image, {0x20: 0x41})                  # REL in percent (bit 0) and the ratio
    e.command({'dial': 2})
    e.advance(3.0)
    e.command({'input': {'kind': 'V', 'value': 1.0, 'ac': False}})
    e.advance(4.0)
    T.press(e, 'REL', gap=1.0)
    e.command({'input': {'kind': 'V', 'value': 1.5, 'ac': False}})
    e.advance(4.0)
    main, v, a, m = look(e)
    w0 = e.snapshot()['lcd'][0]
    T.check('REL percent with a ratio: percent (50 %), the % sign on, V and A off', (w0 >> 17 & 1) == 1 and (v, a) == (0, 0)
            and abs(float(main) - 50.0) < 1.0, 'main %r V%d A%d %%%d' % (main, v, a, w0 >> 17 & 1))


def other_modes(image):
    print('T3 other modes')
    e = T.make(image, {0x20: 0x80})                  # ratio code 4
    e.command({'jack': 'mA', 'plugged': True})
    e.command({'dial': 8})
    e.advance(3.0)
    T.long_press(e, 'PEAK')
    e.command({'input': {'kind': 'A', 'value': 0.0123, 'ac': False}})
    e.advance(3.0)
    b13 = frame_byte13(e, 1.5)
    main, v, a, m = look(e)
    T.check('DCmA (mode %d) with a ratio: 12.3 mA as always, A and m on, V off' % T.rd(e, ADDR_MODE),
            T.rd(e, ADDR_MODE) == 19 and abs(float(main) - 12.3) < 0.2 and (v, a, m) == (0, 1, 1), 'main %r V%d A%d m%d' % (main, v, a, m))
    T.check('DCmA: frame byte 13 bits 7..5 = 0', b13 is not None and b13 >> 5 == 0, 'byte 13 %r' % b13)


def to_clp(e):
    e.m.uc.mem_write(ADDR_ITEM, bytes([ITEM_DSP]))
    T.press(e, 'SETUP', gap=0.5)
    e.advance(0.6)
    return T.rd(e, ADDR_ITEM) == ITEM_CLP


def seen_sub(e, want):
    """The sub display text over one second (an edited value blinks): True if `want` was shown.  The decoder reads the
    letter o as the digit 0 or the other way round, so both sides are compared with every 0 read as O."""
    norm = lambda t: t.replace(' ', '').upper().replace('0', 'O')  # noqa: E731
    return norm(want) in {norm(t) for t in T.sample_texts(e, n=5, step=0.25)}


def page(image):
    print('T4 the page CLP')
    e = T.make(image, {0x20: 0x15})                  # bits 0, 2, 4 set, no ratio
    e.command({'dial': 2})
    e.advance(3.0)
    T.check('dSP -> CLP (item 48) in DCV', to_clp(e), 'item %d' % T.rd(e, ADDR_ITEM))
    T.check('sub display "oFF", main display "CLP"', seen_sub(e, 'OFF') and T.DEC.text(e.snapshot()['lcd'])[0].strip().upper() == 'CLP',
            'sub %r main %r' % (T.sub_text(e), T.DEC.text(e.snapshot()['lcd'])[0]))
    before = bytes(e.m.eeprom)
    T.long_press(e, 'SETUP')
    T.check('long SETUP starts the edit', T.rd(e, ADDR_EDIT) == 1)
    for code, text in ((1, '1'), (2, '10'), (3, '100')):
        T.press(e, 'REL', gap=0.5)
        T.check('REL: code %d, sub display "%s"' % (code, text), T.rd(e, SYMS['set_clamp_code']) == code and seen_sub(e, text),
                'code %d sub %r' % (T.rd(e, SYMS['set_clamp_code']), T.sub_text(e)))
    T.check('nothing written before the save', bytes(e.m.eeprom) == before)
    T.long_press(e, 'SETUP')
    e.advance(1.5)
    diff = {i: (before[i], e.m.eeprom[i]) for i in range(len(before)) if before[i] != e.m.eeprom[i]}
    T.check('save wrote only EEPROM 0x020: 0x15 -> 0x75 (bits 4..0 kept)', diff == {0x20: (0x15, 0x75)}, repr(diff))
    T.press(e, 'SETUP', gap=0.5)
    T.check('CLP -> Lt (item 27)', T.rd(e, ADDR_ITEM) == ITEM_LT, 'item %d' % T.rd(e, ADDR_ITEM))
    e.command({'input': {'kind': 'V', 'value': 1.234, 'ac': False}})
    e.advance(4.0)
    check_view(e, 'after leaving the page: 1.234 V at 100 mV/A = 12.34 A', 1, 3)
    # wrap in both directions, no save
    to_clp(e)
    T.long_press(e, 'SETUP')
    T.press(e, 'REL', gap=0.5)
    T.check('REL from 100: "1000" (code 4)', T.rd(e, SYMS['set_clamp_code']) == 4 and seen_sub(e, '1000'))
    T.press(e, 'REL', gap=0.5)
    T.check('REL from 1000: off (code 0)', T.rd(e, SYMS['set_clamp_code']) == 0 and seen_sub(e, 'OFF'))
    T.press(e, 'PEAK', gap=0.5)
    T.check('PEAK from off: "1000" (code 4)', T.rd(e, SYMS['set_clamp_code']) == 4 and seen_sub(e, '1000'))
    T.press(e, 'PEAK', gap=0.5)
    T.check('PEAK from 1000: "100" (code 3)', T.rd(e, SYMS['set_clamp_code']) == 3)
    e.advance(125.0)
    T.check('edit left unsaved: after the timeout the saved code 3 again, EEPROM unchanged',
            T.rd(e, SYMS['set_clamp_code']) == 3 and e.m.eeprom[0x20] == 0x75 and T.rd(e, ADDR_EDIT) == 0,
            'code %d eeprom 0x%02X' % (T.rd(e, SYMS['set_clamp_code']), e.m.eeprom[0x20]))
    e.power_cycle()
    e.command({'dial': 2})
    e.advance(3.0)
    T.check('power cycle: the code 3 from EEPROM 0x020', T.rd(e, SYMS['set_clamp_code']) == 3)
    e.command({'input': {'kind': 'V', 'value': 1.234, 'ac': False}})
    e.advance(4.0)
    check_view(e, 'after the power cycle: 12.34 A', 1, 3)
    # the pages of the other bits keep bits 7..5
    e.m.uc.mem_write(ADDR_ITEM, bytes([0]))
    T.press(e, 'SETUP', gap=0.5)
    T.check('item 0 -> rEL', T.rd(e, ADDR_ITEM) == ITEM_REL, 'item %d' % T.rd(e, ADDR_ITEM))
    T.long_press(e, 'SETUP')
    T.press(e, 'REL', gap=0.5)
    T.long_press(e, 'SETUP')
    e.advance(1.5)
    T.check('rEL save changes only bit 0: 0x75 -> 0x74, bits 7..5 kept', e.m.eeprom[0x20] == 0x74, '0x%02X' % e.m.eeprom[0x20])
    # the other modes with the page
    for name, dial, patch, key in (('ACV', 2, {}, 'MODE'), ('DCmV', 3, {EE_MV_MEMORY: 3}, None), ('ACmV', 3, {EE_MV_MEMORY: 4}, None)):
        e = T.make(image, patch)
        e.command({'dial': dial})
        e.advance(3.0)
        if key:
            T.press(e, key, gap=1.0)
        T.check('%s: dSP -> CLP' % name, to_clp(e), 'mode %d item %d' % (T.rd(e, ADDR_MODE), T.rd(e, ADDR_ITEM)))
    # codes 5..7 and an erased byte read as off, and are kept as found by other pages
    for byte in (0xA0, 0xFF):
        e = T.make(image, {0x20: byte})
        e.command({'dial': 2})
        e.advance(3.0)
        T.check('EEPROM 0x020 = 0x%02X reads as off' % byte, T.rd(e, SYMS['set_clamp_code']) == 0)
    e = T.make(image, {0x20: 0xC0})
    e.command({'dial': 2})
    e.advance(3.0)
    e.m.uc.mem_write(ADDR_ITEM, bytes([0]))
    T.press(e, 'SETUP', gap=0.5)
    T.long_press(e, 'SETUP')
    T.press(e, 'REL', gap=0.5)
    T.long_press(e, 'SETUP')
    e.advance(1.5)
    T.check('code 6 (not a ratio): rEL save keeps bits 7..5 as found, 0xC0 -> 0xC1', e.m.eeprom[0x20] == 0xC1, '0x%02X' % e.m.eeprom[0x20])
    s = e.snapshot()
    T.check('no unmodelled access, no safety violation', s['unmodelled'] == 0 and not s['safety'])


def csv_rows(image, patch, dial, steps):
    """Log with MEM long at interval 1 s while the input steps ((volts, ac, seconds) ...); the data rows and MAX / MIN rows."""
    T.Emulator.sdcard_path, T.Emulator.sdcard_mb = L.card(), 64
    e = T.make(image, {0x1E: 0, 0x18: 0, 0x19: 1, **patch})
    e.command({'dial': dial})
    e.advance(3.0)
    volts, ac, wait = steps[0]
    e.command({'input': {'kind': 'V', 'value': volts, 'ac': ac}})
    e.advance(4.0)
    T.check('logging started', L.start(e))
    for volts, ac, wait in steps:
        e.command({'input': {'kind': 'V', 'value': volts, 'ac': ac}})
        e.advance(wait)
    T.long_press(e, 'MEM')
    e.advance(3.0)
    T.check('logging stopped', T.rd(e, L.ADDR_LOGGING) == 0)
    card = T.Emulator.sdcard_path
    del e                                            # the card image is complete once the meter has written it
    lines = []
    with Volume(card) as v:
        for p, ent in v.walk():
            if p.upper().endswith('.CSV'):
                lines = v.read_chain(ent['cluster'], ent['size']).decode('latin1').split('\r\n')
                break
    data = [ln.split(',') for ln in lines if ln.split(',')[0].isdigit()]
    mm = {tag: [ln.split(',') for ln in lines if ln.startswith(tag + ',')] for tag in ('MAX', 'MIN')}
    return data, mm


def csv(image):
    print('T5 the log file')
    # DCV 1.5 V, ratio 10 mV/A: d = 4, d' = 2: "0150.00" A
    data, mm = csv_rows(image, {0x20: 0x40}, 2, [(1.5, False, 6.0)])
    ok = len(data) >= 3 and all(r[5] == 'A' and re.fullmatch(r'\d{4}\.\d\d', r[4]) and abs(float(r[4]) - 150.0) < 3.0 and r[3] == 'DCV' for r in data)
    T.check('DCV 1.5 V at 10 mV/A: rows "DCV,0150.00,A"', ok, repr(data[:2]))
    ok = all(len(mm[t]) == 1 and mm[t][0][4] == 'A' and re.fullmatch(r'\d{4}\.\d\d', mm[t][0][3]) for t in ('MAX', 'MIN'))
    T.check('the MAX and MIN rows in A with the same places', ok, repr(mm))
    # DCV 100 V, ratio 1 mV/A: d = 2, d' = -1: the six digits of the counts and one zero, unit A
    data, mm = csv_rows(image, {0x20: 0x20}, 2, [(100.0, False, 6.0)])
    ok = len(data) >= 3 and all(r[5] == 'A' and re.fullmatch(r'\d{7}', r[4]) and r[4][6] == '0' and abs(int(r[4][:6]) - 10000) < 100
                                for r in data)
    T.check('DCV 100 V at 1 mV/A (d\' = -1): counts and one zero, "0100000" A', ok, repr(data[:2]))
    ok = all(len(mm[t]) == 1 and mm[t][0][4] == 'A' and re.fullmatch(r'\d{7}', mm[t][0][3]) and mm[t][0][3][6] == '0' for t in ('MAX', 'MIN'))
    T.check('the MAX and MIN rows the same', ok, repr(mm))
    # DCmV 20 mV, ratio 1000 mV/A: d = 3, d' = 6: milliamperes, the point after 3 places: "020.000" mA
    data, mm = csv_rows(image, {0x20: 0x80, EE_MV_MEMORY: 3}, 3, [(0.0200, False, 6.0)])
    ok = len(data) >= 3 and all(r[5] == 'mA' and re.fullmatch(r'\d{3}\.\d{3}', r[4]) and abs(float(r[4]) - 20.0) < 0.5 and r[3] == 'DCmV'
                                for r in data)
    T.check('DCmV 20 mV at 1000 mV/A: "020.000" mA', ok, repr(data[:2]))
    ok = all(len(mm[t]) == 1 and mm[t][0][4] == 'mA' for t in ('MAX', 'MIN'))
    T.check('the MAX and MIN rows in mA', ok, repr(mm))
    # no ratio: the volt rows as before
    data, mm = csv_rows(image, {}, 2, [(1.5, False, 6.0)])
    ok = len(data) >= 3 and all(r[5] == 'V' and re.fullmatch(r'\d\d\.\d{4}', r[4]) and abs(float(r[4]) - 1.5) < 0.02 for r in data)
    T.check('no ratio: "01.5000" V as before', ok, repr(data[:2]))


ADDR_CAL, ADDR_MEMVIEW = 0x2000808A, 0x20007FE4      # calibration mode, MEM view (ui_clamp_view reads both)


def rel_beyond(image):
    print('T6 REL difference beyond 99999: digits, point and unit agree')
    # DCmV range 0 (50 mV, d = 3) at 100 mV per A: REL on -53 mV, then +53 mV (and the mirror): D = +-106000 counts, drawn as
    # D / 10 with d - 1 = 2 places: 10600 -> d' = 2 + 3 - 1 = 4 -> "1.0600" A (1.06 A), unit A with the m sign off.
    # Before the fix the unit signs used d = 3 (d' = 5, milliamperes: m on) while the digits had d' = 4.
    for base, now_, sign in ((-0.053, 0.053, 1), (0.053, -0.053, -1)):
        e = T.make(image, {0x20: 0x60, EE_MV_MEMORY: 3})
        e.command({'dial': 3})
        e.advance(3.0)
        e.command({'input': {'kind': 'V', 'value': base, 'ac': False}})
        e.advance(4.0)
        T.check('DCmV %+g V: range 0, d = 3' % base, T.rd(e, ADDR_MODE) == 3 and T.rd(e, ADDR_RANGE) == 0 and T.rd(e, ADDR_DP) == 3,
                'mode %d range %d d %d' % (T.rd(e, ADDR_MODE), T.rd(e, ADDR_RANGE), T.rd(e, ADDR_DP)))
        T.press(e, 'REL', gap=1.0)
        e.command({'input': {'kind': 'V', 'value': now_, 'ac': False}})
        e.advance(4.0)
        counts = s32(e, ADDR_D)
        T.check('REL on %+g V, then %+g V: D beyond 99999 (%d)' % (base, now_, counts), sign * counts > 99999, 'D %d' % counts)
        unit, places = check_view(e, 'REL difference %+d counts at 100 mV/A' % counts, 3, 3)
        T.check('the unit is A (not mA) with the point at 4 places', (unit, places) == ('A', 4), '%s %d' % (unit, places))
        s = e.snapshot()
        T.check('no unmodelled access, no safety violation', s['unmodelled'] == 0 and not s['safety'])


def byte13_gates(image):
    print('T7 byte 13 bits 7..5 only while the display shows the clamp view')
    e = T.make(image, {0x20: 0x40})                  # ratio code 2: 10 mV per A
    e.command({'dial': 2})
    e.advance(3.0)
    T.long_press(e, 'PEAK')                          # Bluetooth on: the status frames
    e.command({'input': {'kind': 'V', 'value': 1.2, 'ac': False}})
    e.advance(4.0)
    b13 = frame_byte13(e, 1.3)
    T.check('DCV with the ratio: bits 7..5 = 2', b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    for name, addr in (('calibration mode', ADDR_CAL), ('MEM view', ADDR_MEMVIEW)):
        e.m.uc.mem_write(addr, bytes([1]))
        b13 = frame_byte13(e, 1.3)
        T.check('%s: bits 7..5 = 0' % name, T.rd(e, addr) == 1 and b13 is not None and b13 >> 5 == 0, 'byte 13 %r flag %d' % (b13, T.rd(e, addr)))
        e.m.uc.mem_write(addr, bytes([0]))
        b13 = frame_byte13(e, 1.3)
        T.check('%s left: bits 7..5 = 2 again' % name, b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    T.check('the CLP page is reached', to_clp(e), 'item %d' % T.rd(e, ADDR_ITEM))
    b13 = frame_byte13(e, 1.3)
    T.check('page CLP (the main display shows its name): bits 7..5 = 0', b13 is not None and b13 >> 5 == 0, 'byte 13 %r' % b13)
    T.press(e, 'SETUP', gap=0.5)
    T.check('CLP -> Lt', T.rd(e, ADDR_ITEM) == ITEM_LT, 'item %d' % T.rd(e, ADDR_ITEM))
    b13 = frame_byte13(e, 1.3)
    T.check('page left: bits 7..5 = 2 again', b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    s = e.snapshot()
    T.check('no unmodelled access, no safety violation', s['unmodelled'] == 0 and not s['safety'])
    # the pages Sd, tHI and tLo take the main digits and the units stay those of the voltage: no clamp view, bits 7..5 = 0
    e = T.make(image, {0x20: 0x40})
    e.command({'dial': 2})
    e.advance(3.0)
    T.long_press(e, 'PEAK')                          # Bluetooth on: the status frames
    e.command({'input': {'kind': 'V', 'value': 1.2, 'ac': False}})
    e.advance(4.0)
    T.press(e, 'MINMAX', gap=0.5)
    T.check('MIN/MAX on, page n', T.rd(e, ADDR_MM_STATE) != 0 and T.rd(e, ADDR_ITEM) == 32,
            'state %d item %d' % (T.rd(e, ADDR_MM_STATE), T.rd(e, ADDR_ITEM)))
    b13 = frame_byte13(e, 1.3)
    T.check('MIN/MAX on, page n (the reading shows amperes): bits 7..5 = 2', b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    for name, item in (('Sd', 33), ('tHI', 34), ('tLo', 35)):
        T.press(e, 'SETUP', gap=0.6)
        b13 = frame_byte13(e, 1.3)
        T.check('page %s (item %d): bits 7..5 = 0' % (name, T.rd(e, ADDR_ITEM)), T.rd(e, ADDR_ITEM) == item and b13 is not None
                and b13 >> 5 == 0, 'item %d byte 13 %r' % (T.rd(e, ADDR_ITEM), b13))
    T.press(e, 'SETUP', gap=0.6)
    T.press(e, 'SETUP', gap=0.6)
    b13 = frame_byte13(e, 1.3)
    T.check('pages left (item %d), MIN/MAX still on: bits 7..5 = 2 again' % T.rd(e, ADDR_ITEM),
            T.rd(e, ADDR_ITEM) == 0 and T.rd(e, ADDR_MM_STATE) != 0 and b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    s = e.snapshot()
    T.check('no unmodelled access, no safety violation', s['unmodelled'] == 0 and not s['safety'])
    e = T.make(image, {0x20: 0x41})                  # REL in percent (bit 0) and the ratio 10 mV per A
    e.command({'dial': 2})
    e.advance(3.0)
    T.long_press(e, 'PEAK')
    e.command({'input': {'kind': 'V', 'value': 1.0, 'ac': False}})
    e.advance(4.0)
    b13 = frame_byte13(e, 1.3)
    T.check('REL off: bits 7..5 = 2', b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    T.press(e, 'REL', gap=1.0)
    e.command({'input': {'kind': 'V', 'value': 1.5, 'ac': False}})
    e.advance(4.0)
    b13 = frame_byte13(e, 1.3)
    w0 = e.snapshot()['lcd'][0]
    T.check('REL as percent (the % sign is on): bits 7..5 = 0', (w0 >> 17 & 1) == 1 and b13 is not None and b13 >> 5 == 0,
            'byte 13 %r %%%d' % (b13, w0 >> 17 & 1))
    print('T9 REL in percent left with a ratio: no % next to the amperes')
    T.press(e, 'REL', gap=1.0)                       # REL off: the view changes from percent to the clamp view
    e.command({'input': {'kind': 'V', 'value': 1.5, 'ac': False}})
    e.advance(4.0)
    T.check('REL off: the reading is the voltage again', T.rd(e, ADDR_REL) == 0, 'rel_active %d' % T.rd(e, ADDR_REL))
    check_view(e, 'REL off: 1.5 V at 10 mV/A = 150 A', 1, 2)
    w0 = e.snapshot()['lcd'][0]
    T.check('REL off: the % sign is out', (w0 >> 17 & 1) == 0, 'w0 0x%08X' % w0)
    b13 = frame_byte13(e, 1.3)
    T.check('REL off: bits 7..5 = 2 again', b13 is not None and b13 >> 5 == 2, 'byte 13 %r' % b13)
    s = e.snapshot()
    T.check('no unmodelled access, no safety violation', s['unmodelled'] == 0 and not s['safety'])


def lamp_test(image):
    print('T8 the lamp test of the power-on window with a ratio')
    lamp = T.symbol(os.path.join(os.path.dirname(os.path.abspath(image)), '121gw.map'), 'ui_lamp_test')
    for name, dial, patch, code in (('DCV', 2, {}, 2), ('DCmV', 3, {EE_MV_MEMORY: 3}, 4), ('DCV, no ratio', 2, {}, 0)):
        e = T.make(image, {**patch, 0x20: code << 5})
        for k in ('RANGE', 'MINMAX'):
            e.command({'key': k, 'down': True})
        e.command({'dial': dial})
        e.advance(1.0)
        w = e.snapshot()['lcd']
        full = all(w[i] == 0xFFFFFFFF for i in (0, 2, 4, 6)) and all(w[i] & 0xFFF == 0xFFF for i in (1, 3, 5, 7))
        T.check('%s, ratio code %d: lamp test on (ui_lamp_test = %d), every segment lit' % (name, code, T.rd(e, lamp)),
                T.rd(e, lamp) == 1 and full, 'lcd %s' % ' '.join('%08X' % x for x in w))
        for k in ('RANGE', 'MINMAX'):
            e.command({'key': k, 'down': False})
        e.advance(0.2)
        w = e.snapshot()['lcd']
        T.check('%s, ratio code %d: still every segment until the window closes' % (name, code),
                all(w[i] == 0xFFFFFFFF for i in (0, 2, 4, 6)), 'lcd %s' % ' '.join('%08X' % x for x in w))
        e.advance(3.0)
        main, v, a, m = look(e)
        T.check('%s, ratio code %d: the window closed, the reading is shown (lamp test off, %s)' % (name, code, 'A' if code else 'V'),
                T.rd(e, lamp) == 0 and (a == 1) == (code != 0) and (v == 1) == (code == 0), 'main %r V%d A%d m%d' % (main, v, a, m))


CASES = {'matrix': matrix, 'hold_rel': hold_rel, 'other_modes': other_modes, 'page': page, 'csv': csv,
         'rel_beyond': rel_beyond, 'byte13_gates': byte13_gates, 'lamp_test': lamp_test}


def run_tests(image, names=tuple(CASES)):
    mp = os.path.join(os.path.dirname(os.path.abspath(image)), '121gw.map')
    SYMS['set_clamp_code'] = T.symbol(mp, 'set_clamp_code')
    for n in names:
        CASES[n](image)


def main(argv):
    names = list(CASES)
    if argv and argv[0] == '--cases':
        names = argv[1].split(',')
        argv = argv[2:]
        unknown = [n for n in names if n not in CASES]
        if unknown:
            raise SystemExit('unknown case(s) %s; cases: %s' % (', '.join(unknown), ', '.join(CASES)))
    image = argv[0] if argv else os.path.join(T.ROOT, 'firmware', 'build', 'EEVBlog.bin')
    run_tests(image, names)
    print('RESULT', ('FAIL: ' + ', '.join(T.fails)) if T.fails else 'ALL PASS', flush=True)
    return 1 if T.fails else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
