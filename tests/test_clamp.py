"""Current clamp (firmware F-1.35, GitHub issue 39): the status frame keeps the volt counts, mode and range and carries
the ratio code in byte [13] bits 7..5; the companion shows amps with the point moved, mA when more than four places
would follow the point, OFL when the current does not fit the five digits; the METER SETTINGS row CLP is bits 7..5 of
the setting id 0x020 and only listed for F-1.35 and newer."""
import unittest

from support import settings
from meter121 import display


def frame(mode, rng, mag, clamp=0, neg=False, ovl=False):
    """A status frame 0xF2 of `mag` counts in mode / range with the clamp code in byte [13] bits 7..5."""
    f = bytearray(19)
    f[0] = 0xF2
    f[5] = mode | ((mag >> 16) & 3) << 6
    f[6] = rng | (0x40 if neg else 0) | (0x80 if ovl else 0)
    f[7], f[8] = (mag >> 8) & 0xFF, mag & 0xFF
    f[13] = (clamp << 5) | 0x10                                  # bargraph off, clamp code
    for b in f[:18]:
        f[18] ^= b
    return bytes(f)


class ClampDecode(unittest.TestCase):
    def shown(self, mode, rng, mag, clamp, **kw):
        r = display.decode(frame(mode, rng, mag, clamp, **kw), 0)
        return r['text'], r['unit'], r['value']

    def test_off_is_volts(self):
        self.assertEqual(self.shown(1, 0, 2534, 0), ('0.2534', 'V', 0.2534))
        self.assertEqual(self.shown(3, 1, 25340, 0), ('253.40', 'mV', 253.4))

    def test_dcv_every_ratio(self):
        # DCV range 0: 0.2534 V (4 places after the point)
        self.assertEqual(self.shown(1, 0, 2534, 1), ('253.4', 'A', 253.4))          # 1 mV/A
        self.assertEqual(self.shown(1, 0, 2534, 2), ('25.34', 'A', 25.34))          # 10 mV/A
        self.assertEqual(self.shown(1, 0, 2534, 3), ('2.534', 'A', 2.534))          # 100 mV/A
        self.assertEqual(self.shown(1, 0, 2534, 4), ('0.2534', 'A', 0.2534))        # 1 V/A

    def test_mv_ranges_and_milliamps(self):
        # DCmV range 1: 253.40 mV (2 places)
        self.assertEqual(self.shown(3, 1, 25340, 1), ('253.40', 'A', 253.4))        # 1 mV/A: mV reads as A
        self.assertEqual(self.shown(3, 1, 25340, 2), ('25.340', 'A', 25.34))
        self.assertEqual(self.shown(3, 1, 25340, 3), ('2.5340', 'A', 2.534))
        self.assertEqual(self.shown(3, 1, 25340, 4), ('253.40', 'mA', 253.4))       # 5 places -> mA, 2 places
        # DCmV range 0: 50.000 mV at 100 mV/A = 0.5 A -> 500.00 mA
        self.assertEqual(self.shown(3, 0, 50000, 3), ('500.00', 'mA', 500.0))

    def test_acv_and_acmv(self):
        self.assertEqual(self.shown(2, 0, 12345, 2), ('123.45', 'A', 123.45))       # 1.2345 V at 10 mV/A
        self.assertEqual(self.shown(4, 0, 12345, 4), ('12.345', 'mA', 12.345))      # 12.345 mV at 1 V/A

    def test_too_large_shows_ofl(self):
        r = display.decode(frame(1, 3, 2300, 1), 0)                                 # 230.0 V at 1 mV/A
        self.assertEqual((r['text'], r['unit'], r['value'], r['overload']), ('OFL', 'A', None, True))
        r = display.decode(frame(1, 2, 2300, 2), 0)                                 # 23.00 V at 10 mV/A: 2300 A, no point
        self.assertEqual((r['text'], r['unit']), ('2300', 'A'))
        r = display.decode(frame(1, 3, 2300, 2), 0)                                 # 230.0 V at 10 mV/A -> OFL
        self.assertEqual(r['text'], 'OFL')

    def test_sign_and_meter_overload(self):
        self.assertEqual(self.shown(1, 0, 2534, 2, neg=True), ('-25.34', 'A', -25.34))
        r = display.decode(frame(1, 0, 0, 2, ovl=True), 0)
        self.assertEqual((r['text'], r['unit']), ('OFL', 'A'))

    def test_other_modes_ignore_the_bits(self):
        r = display.decode(frame(19, 0, 12345, 2), 0)                               # DCmA keeps mA
        self.assertEqual((r['text'], r['unit']), ('1.2345', 'mA'))
        r = display.decode(frame(9, 1, 4701, 2), 0)                                 # OHM
        self.assertEqual(r['unit'], 'Ω')

    def test_codes_5_to_7_are_off(self):
        self.assertEqual(self.shown(1, 0, 2534, 5), ('0.2534', 'V', 0.2534))
        self.assertEqual(self.shown(1, 0, 2534, 7), ('0.2534', 'V', 0.2534))


class ClampSetting(unittest.TestCase):
    def test_row_is_bits_7_to_5_of_0x020(self):
        s = settings.BY_ID[0x520]
        self.assertEqual((s.code, s.wire, s.shift, s.mask, s.min, s.max), ('CLP', 0x020, 5, 7, 0, 4))
        self.assertEqual(s.choices, ['off', '1 mV/A', '10 mV/A', '100 mV/A', '1 V/A'])
        raw = 0b00011011                                    # REL percent, trigger on change, clock 12 h, anti-hunting on
        self.assertEqual(settings.field(s, raw), 0)
        new = settings.merge(s, raw, 3)
        self.assertEqual(new, raw | (3 << 5))               # the other bits stay
        self.assertEqual(settings.field(s, new), 3)
        self.assertEqual(settings.field(settings.BY_ID[0x420], new), 1)

    def test_row_only_for_f135_and_newer(self):
        ids = lambda **kw: [r.id for r in settings.Settings(None, **kw).rows()]
        self.assertIn(0x520, ids(clamp=True))
        self.assertNotIn(0x520, ids(clamp=False))


if __name__ == '__main__':
    unittest.main()
