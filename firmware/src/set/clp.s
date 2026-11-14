@ =============================================================================
@ clp.s - SET: SETUP page CLP, the current clamp ratio of the voltage modes (production image only, DESIGN.md 10 IMP-68,
@ FEATURES.md 2.41)
@
@ SET module.  A current clamp with a voltage output (a Hall clamp, a shunt with an amplifier) is read with the meter's
@ voltage modes; the page CLP (setup item 48, offered in DCV, ACV, DCmV and ACmV, after dSP, GitHub issue 39) tells the
@ meter how many millivolt the clamp gives per ampere, so that the main display shows amperes:
@     code  sub display  clamp ratio
@     0     "  oFF"      off: the voltage, as always (default)
@     1     "    1"      1 mV per A
@     2     "   10"      10 mV per A
@     3     "  100"      100 mV per A
@     4     " 1000"      1000 mV per A (1 V per A)
@ The main display shows the page name "  CLP" while the page is selected (ui/hunt.s draws it, as it draws the values of the
@ pages HUnt and CHIrP); the sub display shows the ratio, right-aligned.  Long SETUP starts the edit (the sub display
@ blinks), REL steps up, PEAK steps down, long SETUP again saves, SETUP short ends the edit without a save, the edit times
@ out after about 2 minutes (fn_0800D190 then re-reads the EEPROM through set_relp_load).  What the setting does to the
@ readings is ui/clamp.s (display), ble/packet.s (frame byte 13) and log/csv.s (log file).
@ Storage: bits 7..5 of EEPROM 0x020 (EEPROM_MAP.md 4.1, owner approval 2026-11-13), the code 0..4; the other bits of the byte
@ belong to the pages rEL, tr, CL and HUnt (relp.s, logpg.s) and are kept by the save (set_b20_store, mask 0xE0).  The RAM
@ copy set_clamp_code (relp.s) holds the code being edited while the page is in edit mode, else the saved one.
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

    .equ SET_CLP_CODE_MAX,    4             @ highest ratio code: 1000 mV per A
    .equ set_clp_redraw,      v_20008062    @ sub display redraw request
    .equ set_clp_commit_req,  v_2000805B    @ LCD commit request
    .equ set_clp_main_req,    v_2000805F    @ main display redraw request (the page's name is drawn there, ui/hunt.s)

    .section .rodata.set.clp, "a", %progbits
@ set_clp_texts: the sub display text per code, five characters each (right-aligned numbers, "oFF" as the page HUnt writes it)
set_clp_texts:
    .ascii "  oFF"
    .ascii "    1"
    .ascii "   10"
    .ascii "  100"
    .ascii " 1000"
    .size set_clp_texts, . - set_clp_texts

    .section .text.set.clp, "ax", %progbits

@ -----------------------------------------------------------------------------
@ set_clp_edit - edit action of the page CLP (setup item 48)
@ Replaces : nothing in v2.05 (new; model: set_lp_edit, a stepped value)
@ In       : r0 = action: 1 up, 2 down, 100 save
@ Out      : -
@ Clobbers : r0-r3, r12, flags (AAPCS)
@ Context  : main only (fn_080165F4; bftset.s does not call it: a Bluetooth PUT of 0x020 stores through set_b20_store)
@ RAM      : reads and writes set_clamp_code; writes set_clp_redraw
@ Note     : up: off, 1, 10, 100, 1000 mV/A, then off again; down in the reverse order; the readings follow at once (the
@            code is in RAM).  Save writes bits 7..5 of EEPROM 0x020 through set_b20_store (read-modify-write of those
@            bits only, set_nvm_store, NVM skips a byte that does not change).
@ -----------------------------------------------------------------------------
FUNC set_clp_edit
    push {r4, lr}
    uxtb r4, r0                             @ action
    ldr r1, =set_clamp_code
    ldrb r0, [r1]                           @ r0 = the code
    cmp r4, #1
    beq .Lcpe_up
    cmp r4, #2
    beq .Lcpe_down
    lsls r1, r0, #5                         @ save (100): bits 7..5 = the code
    movs r0, #0xE0                          @ mask: bits 7..5
    bl set_b20_store
    b .Lcpe_redraw
.Lcpe_up:
    adds r0, r0, #1
    cmp r0, #SET_CLP_CODE_MAX
    bls 1f
    movs r0, #0                             @ above 1000 mV/A: off
    b 1f
.Lcpe_down:
    subs r0, r0, #1
    bpl 1f
    movs r0, #SET_CLP_CODE_MAX              @ below off: 1000 mV/A
1:  strb r0, [r1]                           @ the new code (r1 = set_clamp_code in both branches)
.Lcpe_redraw:
    movs r0, #1
    ldr r1, =set_clp_redraw
    strb r0, [r1]                           @ redraw the sub display
    pop {r4, pc}
ENDFUNC set_clp_edit

@ -----------------------------------------------------------------------------
@ set_clp_draw - draw the page CLP: the ratio on the sub display (the name is drawn on the main display by ui_hunt_draw)
@ Replaces : nothing in v2.05 (new; fn_0801797C calls it for item 48)
@ In/Out   : - / -
@ Clobbers : r0-r3, r12, flags (AAPCS)
@ Context  : main only (fn_0801797C)
@ RAM      : reads set_clamp_code; writes lcd_shadow (sub minus and decimal points off, five characters), commit request,
@            the main display redraw request 0x2000805F (so that the name appears when the page is entered)
@ -----------------------------------------------------------------------------
FUNC set_clp_draw
    push {r4, lr}
    ldr r0, =lcd_shadow
    ldr r1, [r0, #0x10]
    bic r1, r1, #0x20000000                 @ sub minus sign off
    str r1, [r0, #0x10]
    movs r0, #0                             @ decimal point code 0: none
    bl fn_08017FB4                          @ clear the sub display decimal points
    ldr r0, =set_clamp_code
    ldrb r0, [r0]
    cmp r0, #SET_CLP_CODE_MAX
    bls 1f
    movs r0, #0                             @ the code is kept in range; this keeps the index in the table
1:  add r0, r0, r0, lsl #2                  @ code * 5
    ldr r1, =set_clp_texts
    add r1, r1, r0                          @ r1 = text of the code
    movs r0, #1                             @ sub display
    bl lcd_draw_5char
    movs r0, #1
    ldr r1, =set_clp_commit_req
    strb r0, [r1]                           @ commit the shadow
    ldr r1, =set_clp_main_req
    strb r0, [r1]                           @ the main display shows the page name: draw it
    pop {r4, pc}
ENDFUNC set_clp_draw

    .ltorg
