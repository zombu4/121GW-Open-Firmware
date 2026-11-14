@ =============================================================================
@ clamp.s - UI: the current clamp view of the voltage modes (production image only, DESIGN.md 10 IMP-68, FEATURES.md 2.41)
@
@ With the SETUP page CLP (set/clp.s) at 1, 10, 100 or 1000 mV per A, the voltage modes DCV (mode 1), ACV (2), DCmV (3) and
@ ACmV (4) show on the main display the current of a clamp with a voltage output instead of the voltage.  The reading D is
@ counts of the range: D x 10^-d volt (modes 1, 2) or millivolt (modes 3, 4), d = the decimal point code of the range
@ (0x2000803F, decimal_position_for_mode_range, 0..4).  The current is that voltage divided by the ratio r (mV per A):
@     I = D x 10^-d x u / r,   u = 1 V (modes 1, 2) or 1 mV (modes 3, 4),   r = 10^(code - 1) mV/A (code 1..4 = 1..1000)
@ so the same counts are read with the point d' = d + code - 4 (modes 1, 2) or d + code - 1 (modes 3, 4) in amperes:
@     0 <= d' <= 4   the digits are unchanged, the point at d', unit A
@     d' > 4         unit mA (the m prefix with A), the point at d' - 3 (d' <= 7, so the point is at 1..4)
@     d' < 0         the current does not fit the five digits: the meter's own overload text " OFL " (the OL flag is not
@                    set, the reading itself is valid), unit A
@ The counts D are not touched: the measurement, MIN/MAX, statistics, REL, PEAK, HOLD, the bargraph and its labels, the HV
@ sign, the sub display and the BLE value 0x20007D70 (counts, as ui_relpct_draw leaves it) keep the voltage.  Only the main
@ digits, their point and the unit signs change: ui_clamp_show draws the digits, ui_relpct_mask (relpct.s) applies the unit
@ signs at every full commit (V off, A on, m on for mA), the point of a new range is ui_clamp_point.  The same mapping is
@ written to the log file (log/csv.s) and, as the code, to bits 7..5 of byte 13 of the BLE status frame (ble/packet.s).
@ When the view is shown (ui_clamp_view): the setting not off, a mode 1..4, outside calibration mode (0x2000808A), not in the
@ MEM view (0x20007FE4), not on the page CLP (ui_hunt_view: its name is on the main display) and not while REL is shown in
@ percent (ui_relpct_view comes first: a percent of the current is the percent of the voltage).  The pages that take the
@ main digits (Sd, tHI, tLo, HUnt, CHIrP, CLP) and the loop and RTD views are drawn before it (fn_08016E08).
@ The decimal point of the digits is one rule for the draw and the unit signs: ui_clamp_dpos (D / 10 of a REL difference
@ beyond 99999 has one digit after the point fewer, so d' and the unit kind follow it).  ble/packet.s sends the code of
@ ui_clamp_view, so byte 13 carries it only while the clamp view is shown.
@
@ Context: main only.
@ =============================================================================

    .syntax unified
    .cpu cortex-m3
    .thumb
    .include "macros.inc"
    .include "hw.inc"
    .include "ram_v205.inc"
    .include "config.inc"

    .equ ui_clamp_cal,        v_2000808A    @ calibration mode (the view is off)
    .equ ui_clamp_mem,        v_20007FE4    @ MEM view (the view is off)
    .equ ui_clamp_dp,         v_2000803F    @ decimal point code d of the displayed range (0..4)
    .equ ui_clamp_reading,    v_20007D64    @ displayed reading D (signed counts), the value fn_08016E08 draws
    .equ ui_clamp_last_drawn, v_20007D70    @ last drawn main value, the BLE value
    .equ UI_CLAMP_KIND_A,     1             @ ui_clamp_calc kind: amperes
    .equ UI_CLAMP_KIND_MA,    2             @ ui_clamp_calc kind: milliamperes
    .equ UI_CLAMP_KIND_OFL,   3             @ ui_clamp_calc kind: does not fit the display

    .section .text.ui.clamp, "ax", %progbits

@ -----------------------------------------------------------------------------
@ ui_clamp_code - the clamp ratio code that applies to a mode
@ Replaces : nothing in v2.05 (new)
@ In       : r0 = mode (any byte value)
@ Out      : r0 = 1..4 the ratio code when the mode is DCV, ACV, DCmV or ACmV and the setting is not off, else 0
@ Clobbers : flags
@ Context  : main only (ui_clamp_view, ble/packet.s, log/csv.s)
@ RAM      : reads set_clamp_code
@ -----------------------------------------------------------------------------
FUNC ui_clamp_code
    subs r0, r0, #1
    cmp r0, #3
    bhi 1f                                  @ mode 0 (wraps to a large value) or above 4: no clamp
    ldr r0, =set_clamp_code
    ldrb r0, [r0]                           @ 0 off, 1..4
    bx lr
1:  movs r0, #0
    bx lr
ENDFUNC ui_clamp_code

@ -----------------------------------------------------------------------------
@ ui_clamp_calc - the current reading for a mode, a decimal point code and the clamp ratio
@ Replaces : nothing in v2.05 (new)
@ In       : r0 = mode 1..4 (the caller has tested ui_clamp_code), r1 = d, the digits after the point of the voltage (0..4)
@ Out      : r0 = kind: 1 amperes, 2 milliamperes, 3 does not fit; r1 = the point to draw (kind 1: d', kind 2: d' - 3,
@            kind 3: 0); r2 = d' (signed: d + code - 4 in modes 1 and 2, d + code - 1 in modes 3 and 4, set_clamp_code = code)
@ Clobbers : r3, flags
@ Context  : main only
@ RAM      : reads set_clamp_code
@ Note     : kind 2 has d' 5..7, so the point of the milliamperes is 2..4 (a larger d' would need d above 4: not in the table).
@ -----------------------------------------------------------------------------
FUNC ui_clamp_calc
    ldr r2, =set_clamp_code
    ldrb r2, [r2]                           @ r2 = code
    uxtb r1, r1
    add r1, r1, r2                          @ d + code
    cmp r0, #3
    ite lo
    movlo r3, #4                            @ DCV, ACV (modes 1, 2): the unit is the volt, base 4 (the ratio code 4 = 1 V/A: d' = d)
    movhs r3, #1                            @ DCmV, ACmV (modes 3, 4): the unit is the millivolt, base 1 (code 1 = 1 mV/A: d' = d)
    subs r2, r1, r3                         @ r2 = d' = d + code - base (signed)
    bmi 3f                                  @ d' < 0: does not fit
    cmp r2, #4
    bgt 2f                                  @ d' > 4: milliamperes
    movs r0, #UI_CLAMP_KIND_A
    mov r1, r2                              @ the point at d'
    bx lr
2:  movs r0, #UI_CLAMP_KIND_MA
    subs r1, r2, #3                         @ the point at d' - 3 (the same counts in mA)
    bx lr
3:  movs r0, #UI_CLAMP_KIND_OFL
    movs r1, #0                             @ no point under the overload text
    bx lr
ENDFUNC ui_clamp_calc

@ -----------------------------------------------------------------------------
@ ui_clamp_dpos - the decimal point code the main draw of the reading D uses
@ Replaces : nothing in v2.05 (new; the rule of fn_08016E08 0x08017046..0x0801705C, now shared)
@ In       : r0 = D, the displayed reading (signed counts)
@ Out      : r0 = d, the digits after the point that the digits of D are drawn with: the code of the displayed range
@            (0x2000803F) while D is inside -99999..99999; outside (REL only, D / 10 is drawn) one less, but not below 0
@ Clobbers : r1, flags
@ Context  : main only (fn_08016E08 for the draw, ui_clamp_kind for the unit signs)
@ RAM      : reads 0x2000803F
@ Note     : One rule for the digits, the point and the unit: the draw passes this d to ui_clamp_show (which takes d' and the
@            kind from it) and ui_clamp_kind takes the unit kind from it, so they always agree.
@ -----------------------------------------------------------------------------
FUNC ui_clamp_dpos
    ldr r1, =0x0001869F                     @ 99999
    adds r0, r0, r1                         @ D + 99999: 0..199998 exactly when D is inside -99999..99999 (a wrap is above)
    ldr r1, =0x00030D3E                     @ 199998
    cmp r0, r1
    ldr r0, =ui_clamp_dp
    ldrb r0, [r0]                           @ d of the displayed range (the load keeps the flags)
    bls 1f                                  @ D inside the five digits: d
    cbz r0, 1f                              @ d is 0: stays 0 (fn_08016E08 does not go below 0)
    subs r0, r0, #1                         @ D / 10 has one digit after the point fewer
1:  bx lr
ENDFUNC ui_clamp_dpos

@ -----------------------------------------------------------------------------
@ ui_clamp_view - is the current clamp view shown?
@ Replaces : nothing in v2.05 (new)
@ In       : r0 = mode
@ Out      : r0 = the ratio code 1..4 while the view is shown, else 0
@ Clobbers : r1-r3, r12, flags (AAPCS)
@ Context  : main only (ui_clamp_show, ui_clamp_kind, ui_clamp_point, ble/packet.s)
@ RAM      : reads set_clamp_code, 0x2000808A, 0x20007FE4, set_item, and what ui_relpct_view reads
@ Note     : the page CLP (set_item 48) is not the view: the main display shows its name there (ui_hunt_view)
@ -----------------------------------------------------------------------------
FUNC ui_clamp_view
    push {r4, lr}
    bl ui_clamp_code                        @ 0 for another mode or the setting off
    movs r4, r0
    cbz r4, 2f
    ldr r0, =ui_clamp_cal
    ldrb r0, [r0]
    cbnz r0, 1f                             @ calibration mode: the voltage
    ldr r0, =ui_clamp_mem
    ldrb r0, [r0]
    cbnz r0, 1f                             @ MEM view: the recalled record is shown as it was taken
    bl ui_hunt_view
    cbnz r0, 1f                             @ the page CLP (or HUnt, CHIrP) owns the main digits: no view
    bl ui_relpct_view
    cbz r0, 2f                              @ not REL in percent: the view is shown
1:  movs r4, #0
2:  mov r0, r4
    pop {r4, pc}
ENDFUNC ui_clamp_view

@ -----------------------------------------------------------------------------
@ ui_clamp_kind - the unit signs the clamp view needs at a full commit
@ Replaces : nothing in v2.05 (new; called by ui_relpct_mask)
@ In       : -
@ Out      : r0 = 0 the view is not shown, 3 amperes (the overload text included), 4 milliamperes (the codes of ui_relpct_mask)
@ Clobbers : r1-r3, r12, flags (AAPCS)
@ Context  : main only
@ RAM      : reads meter_mode, 0x2000803F, 0x20007D64
@ Note     : the point position is that of the digits (ui_clamp_dpos of the displayed reading: one less for D / 10), so the
@            unit kind, the point and the digits come from one d
@ -----------------------------------------------------------------------------
FUNC ui_clamp_kind
    push {r4, lr}
    ldr r4, =meter_mode
    ldrb r4, [r4]                           @ r4 = mode
    mov r0, r4
    bl ui_clamp_view
    cbz r0, 1f                              @ not shown: 0
    ldr r0, =ui_clamp_reading
    ldr r0, [r0]                            @ D, the displayed reading
    bl ui_clamp_dpos                        @ r0 = d of the digits (d - 1 when D / 10 is drawn)
    mov r1, r0
    mov r0, r4
    bl ui_clamp_calc
    cmp r0, #UI_CLAMP_KIND_MA
    ite eq
    moveq r0, #4                            @ milliamperes
    movne r0, #3                            @ amperes (or the overload text, whose unit is A)
1:  pop {r4, pc}
ENDFUNC ui_clamp_kind

@ -----------------------------------------------------------------------------
@ ui_clamp_point - the main decimal point to draw for a range
@ Replaces : nothing in v2.05 (new; called where a range is applied: fn_080171FA, ui_bootwin_gate)
@ In       : r0 = d, the decimal point code of the voltage range (0..4)
@ Out      : r0 = the code to draw: d while the view is not shown, else d' (milliamperes: d' - 3, overload text: 0)
@ Clobbers : r1-r3, r12, flags (AAPCS)
@ Context  : main only
@ RAM      : reads meter_mode
@ -----------------------------------------------------------------------------
FUNC ui_clamp_point
    push {r4, r5, lr}
    mov r5, r0                              @ r5 = d (the result when the view is not shown)
    ldr r4, =meter_mode
    ldrb r4, [r4]                           @ r4 = mode
    mov r0, r4
    bl ui_clamp_view
    cbz r0, 1f
    mov r0, r4
    mov r1, r5
    bl ui_clamp_calc                        @ r0 = kind, r1 = the point (0 for the overload text)
    mov r5, r1
1:  mov r0, r5
    pop {r4, r5, pc}
ENDFUNC ui_clamp_point

@ -----------------------------------------------------------------------------
@ ui_clamp_show - draw the reading on the main display as lcd_show_value_pos5_9 does, in amperes while the clamp view is shown
@ Replaces : lcd_show_value_pos5_9 in the draws of the reading D (fn_08016E08, fn_08015934, the REL key's held draws in
@            key/short.s); the other callers draw views of their own and keep calling lcd_show_value_pos5_9
@ In       : r0 = meter mode, r1 = d (digits after the point of the voltage range), r2 = signed value (counts),
@            r3 = 1 skip while hold is active, 0 draw always (the arguments of lcd_show_value_pos5_9)
@ Out      : -
@ Clobbers : r0-r3, r12, flags (r4-r7 saved)
@ Context  : main only
@ RAM      : as lcd_show_value_pos5_9, which it calls for the digits; for the overload text (d' < 0): lcd_shadow (the point
@            and the minus sign off, " OFL "), the BLE value 0x20007D70 = the counts, the commit request 0x2000805B
@ Note     : view not shown: the call is passed on unchanged (a tail call).  Kind 1 and 2: the digits of the counts with the
@            point of ui_clamp_calc.  Kind 3: " OFL " in place of the digits; with r3 = 1 and HOLD active nothing is drawn,
@            as lcd_show_value_pos5_9 skips.
@ -----------------------------------------------------------------------------
FUNC ui_clamp_show
    push {r4-r7, lr}
    mov r4, r0                              @ r4 = mode
    mov r5, r1                              @ r5 = d
    mov r6, r2                              @ r6 = value
    mov r7, r3                              @ r7 = skip while hold
    bl ui_clamp_view
    cbnz r0, 1f
    mov r0, r4                              @ the view is not shown: the plain draw with the caller's arguments
    mov r1, r5
    mov r2, r6
    mov r3, r7
    pop {r4-r7, lr}
    b lcd_show_value_pos5_9
1:  mov r0, r4
    mov r1, r5
    bl ui_clamp_calc                        @ r0 = kind, r1 = the point
    cmp r0, #UI_CLAMP_KIND_OFL
    beq 2f
    mov r0, r4                              @ amperes or milliamperes: the digits of the counts, the point of the current
    mov r2, r6
    mov r3, r7
    bl lcd_show_value_pos5_9
    pop {r4-r7, pc}
2:  cbz r7, 3f
    ldr r0, =hold_active
    ldrb r0, [r0]
    cbnz r0, 4f                             @ HOLD: the held display stays, as lcd_show_value_pos5_9 skips (r3 = 1)
3:  bl ui_stats_text_prep                   @ the main point and minus sign off before the text
    movs r0, #0
    bl fn_080199B4                          @ " OFL " on the main display
    ldr r0, =ui_clamp_last_drawn
    str r6, [r0]                            @ the BLE frame keeps reporting the counts
    movs r0, #1
    ldr r1, =v_2000805B
    strb r0, [r1]                           @ commit request (the text drawer does not set it itself)
4:  pop {r4-r7, pc}
ENDFUNC ui_clamp_show

    .ltorg
