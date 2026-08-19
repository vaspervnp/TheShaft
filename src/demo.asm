; ======================================================================
;
;   THE SHAFT -- DEMO DISC
;
;   A scripted run over a set built from the game's own tiles and
;   compiled sprites, framed 16:9 for video capture.  The mechanic is
;   nailed to a fixed slot near the bottom of the frame; the world
;   slides around him.  He climbs a long ladder, walks the deck, jumps
;   a gap, waits out a steam vent, throws a switch, climbs again,
;   pockets a yellow keycard and walks through the yellow door.
;
;   Why it is drawn the way it is: a full redraw of the 80x152 band is
;   ~420 tiles, about five frames' work.  So the set is kept sparse and
;   held as an OBJECT LIST instead of a tilemap.  Each update erases
;   every object at the camera position this buffer last saw and
;   redraws it at the current one -- tools/demo_scene.py simulates the
;   whole script and refuses to emit a set whose visible-object count
;   would break the frame budget.
;
;   The camera is 24-bit fixed point (8 fractional bits) driven by the
;   interrupt's 50 Hz frame count, not by how long a redraw took, so
;   the beats keep their timing even if the raster is missed -- and the
;   world is free to be taller than a byte of scanlines.
;
; ======================================================================

GA_PORT         equ #7F00
CRTC_SEL        equ #BC00
CRTC_DATA       equ #BD00
PPI_B_HI        equ #F5
SCREEN_A        equ #C000
SCREEN_B        equ #4000
CRTC_R12_A      equ #30
GA_MODE0_NOROM  equ #8C
GA_RAM_BASE     equ #C0
SCR_W_BYTES     equ 80
BAND_ROWS       equ 19          ; 152 lines: the 16:9 window

A_CLIMB         equ 0           ; the five ways he is drawn
A_RIGHT         equ 1
A_LEFT          equ 2
A_JUMP          equ 3
A_STAND         equ 4

E_SWITCH        equ 1           ; what a beat does to the set
E_KEY           equ 2
E_DOOR          equ 3
E_STEAM         equ 4           ; ...or for how long the vent blows

SFX_JUMP        equ 1           ; the game's rising leap chirp
SFX_PING        equ 2           ; ...and its keycard/door/switch ding

BEAT_FRAMES     equ 10          ; one strike every 10 real frames

        org #1000

; ======================================================================
start:
        di
        ld sp,#1000

        ld bc,GA_PORT+GA_MODE0_NOROM
        out (c),c
        ld bc,GA_PORT+GA_RAM_BASE
        out (c),c

        ; The AY sits behind the 8255; BASIC can leave port A as an
        ; INPUT, and then every register write goes nowhere.
        ld bc,#F782             ; port A output, port B input, port C output
        out (c),c

        ld hl,int_stub          ; 300 Hz tick -> 50 Hz frame count
        ld de,#0038
        ld bc,int_stub_end-int_stub
        ldir
        im 1

        ld hl,zone_palettes     ; the machine-deck colours
        call set_palette
        call clear_buffers

        ld bc,CRTC_SEL+6        ; R6: show only 19 character rows, so
        out (c),c               ; the active picture is a 16:9 band
        ld b,CRTC_DATA/256
        ld a,BAND_ROWS
        out (c),a

        ld a,CRTC_R12_A
        ld (shown_r12),a
        ld bc,CRTC_SEL+12
        out (c),c
        ld b,CRTC_DATA/256
        out (c),a
        ld a,SCREEN_B/256
        ld (draw_page),a
        ei
        call ay_init            ; a defined, silent chip to build on

; ----------------------------------------------------------------------
demo_restart:
        xor a
        ld (beat_phase),a
        ld (env_pos),a
        ld (sfx_timer),a
        call snd_off            ; a strike may still be decaying, and
        ld a,BEAT_FRAMES        ; the branch into here skips env_update
        ld (beat_timer),a
        call scene_reset        ; put back the switch, card and door
        ld hl,demo_script
        ld (seg_ptr),hl
        call seg_load
        ld hl,DEMO_CAMX_LO*256  ; 24-bit: fraction, then two whole bytes
        ld (cam_xf),hl
        ld a,DEMO_CAMX_HI
        ld (cam_xh),a
        ld hl,DEMO_CAMY_LO*256
        ld (cam_yf),hl
        ld a,DEMO_CAMY_HI
        ld (cam_yh),a
        call cam_whole
        ld hl,(cam_ix)          ; both buffers start where he does
        ld (cam_prev),hl
        ld (cam_prev+4),hl
        ld hl,(cam_iy)
        ld (cam_prev+2),hl
        ld (cam_prev+6),hl
        xor a
        ld (anim_ctr),a
        ld (steam_prev),a
        ld (steam_prev+3),a
        ld hl,DEMO_HERO_Y*257   ; same y in both buffers' erase slots
        ld (hero_prev),hl
        call clear_buffers
        ld a,(frame50)
        ld (last_frame),a

main_loop:
        call advance_camera     ; carry set once the script runs out
        jp c,demo_restart

        ; --- erase: everything where THIS buffer last drew it
        call prev_slot
        ld a,(hl)
        ld (pass_x),a
        inc hl
        ld a,(hl)
        ld (pass_x+1),a
        inc hl
        ld a,(hl)
        ld (pass_y),a
        inc hl
        ld a,(hl)
        ld (pass_y+1),a
        ld a,1
        ld (blit_black),a
        call object_pass
        call erase_steam
        call erase_hero

        ; --- draw: the same objects at the camera we are on now
        ld hl,(cam_ix)
        ld (pass_x),hl
        ld hl,(cam_iy)
        ld (pass_y),hl
        xor a
        ld (blit_black),a
        call object_pass
        call draw_steam
        call draw_hero
        call sfx_update         ; the chirp and the ding come first;
        call env_update         ; then the click's decay, then maybe
        call beat_tick          ; a new strike (which yields to them)

        call prev_slot          ; remember where this buffer stands
        ld de,(cam_ix)
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ld de,(cam_iy)
        ld (hl),e
        inc hl
        ld (hl),d

        call frame_sync
        call flip_buffers
        jp main_loop

; prev_slot -- HL -> this buffer's four-byte (cam_x, cam_y) record
prev_slot:
        ld a,(buf_index)
        add a,a
        add a,a
        ld l,a
        ld h,0
        ld de,cam_prev
        add hl,de
        ret

; ======================================================================
; advance_camera -- move by however many 50 Hz frames have elapsed since
; the last update, walking the script as it goes.  Carry = script done.
; ======================================================================
advance_camera:
        ld a,(frame50)
        ld b,a
        ld a,(last_frame)
        ld c,a
        ld a,b
        ld (last_frame),a
        sub c                   ; frames elapsed (wraps cleanly at 256)
        jr nz,ac_have
        ld a,1                  ; never stall: always advance one
ac_have:
        cp 8
        jr c,ac_step
        ld a,8                  ; a long stall must not teleport him
ac_step:
        ld (frames_now),a       ; the sound needs this too: an update is
        ld b,a                  ; not a unit of time, a frame is
ac_loop:
        push bc
        ld a,(seg_frames)
        or a
        jr nz,ac_move
        call seg_load           ; segment spent: take the next one
        jr c,ac_end
        ld a,(seg_frames)
ac_move:
        dec a
        ld (seg_frames),a
        ld hl,seg_elapsed       ; how far into this beat we are
        inc (hl)
        ld hl,cam_xf            ; x += dx, in 24 bits
        ld de,(seg_dx)
        call cam_add
        ld hl,cam_yf
        ld de,(seg_dy)
        call cam_add
        pop bc
        djnz ac_loop
        call cam_whole
        or a                    ; carry clear: keep going
        ret
ac_end:
        pop bc
        scf
        ret

; cam_add -- HL -> {fraction, low, high}, DE = signed 8.8 step
cam_add:
        ld a,d                  ; the STEP's sign, grabbed before D is
        ld (ca_sign),a          ; overwritten by the sum
        push hl
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a                  ; HL = fraction + low byte
        add hl,de
        ex de,hl
        pop hl
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ld a,(ca_sign)          ; sign-extend the step into the top byte
        ld c,0                  ; (LD, BIT, JR and INC HL all leave the
        bit 7,a                 ;  carry alone, so the add's carry is
        jr z,ca_pos             ;  still there for the ADC)
        ld c,#FF
ca_pos:
        ld a,(hl)
        adc a,c
        ld (hl),a
        ret
ca_sign:        defb 0

; cam_whole -- copy the whole parts of both cameras out for rendering
cam_whole:
        ld a,(cam_xf+1)
        ld (cam_ix),a
        ld a,(cam_xh)
        ld (cam_ix+1),a
        ld a,(cam_yf+1)
        ld (cam_iy),a
        ld a,(cam_yh)
        ld (cam_iy+1),a
        ret

; ----------------------------------------------------------------------
; seg_load -- pull one script record in and act on its event.
; ----------------------------------------------------------------------
seg_load:
        ld hl,(seg_ptr)
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld (seg_dx),de
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld (seg_dy),de
        ld a,(hl)
        inc hl
        ld (seg_frames),a
        ld a,(hl)
        inc hl
        ld (seg_anim),a
        ld c,a
        ld a,(hl)
        inc hl
        ld (seg_event),a
        ld (seg_ptr),hl
        xor a
        ld (seg_elapsed),a
        ld a,c
        cp #FF                  ; the end marker?  (CP leaves carry set
        jr z,sl_end             ; for every value below it, so the
        call do_event           ; "keep going" exit must clear it)
        or a
        ret
sl_end:
        scf
        ret

; do_event -- one-shot edits to the set, plus the steam's on/off gate
do_event:
        ld a,(seg_anim)         ; a leap announces itself the moment
        cp A_JUMP               ; its beat begins, like the game's
        jr nz,de_no_jump
        ld a,SFX_JUMP
        call sfx_start
de_no_jump:
        ld a,(seg_event)
        cp E_STEAM
        ld hl,steam_on
        ld (hl),0
        jr nz,de_not_steam
        ld (hl),1
        ret
de_not_steam:
        dec a
        jr z,de_switch
        dec a
        jr z,de_key
        dec a
        ret nz
        ld hl,demo_door_offs    ; the door swings open.  Its five cells
        ld b,DEMO_DOOR_N        ; are scattered through the list, so
        xor a                   ; walk the offsets the tool emitted
        call door_write
        jr de_ping
de_switch:
        ld hl,demo_objects+DEMO_SW_OFF
        ld (hl),DEMO_SW_TILE    ; lever down -> lever up, and green
        jr de_ping
de_key:
        ld hl,demo_objects+DEMO_KEY_OFF
        ld (hl),0               ; pocketed
de_ping:
        ld a,SFX_PING           ; the game dings for all three
        call sfx_start
        ret

; scene_reset -- put the set back the way the script expects to find it
scene_reset:
        ld hl,demo_objects+DEMO_SW_OFF
        ld (hl),DEMO_SW_OFF_TILE
        ld hl,demo_objects+DEMO_KEY_OFF
        ld (hl),DEMO_KEY_TILE
        ld hl,demo_door_offs
        ld b,DEMO_DOOR_N
        ld a,DEMO_DOOR_TILE
        call door_write
        ld hl,(demo_door_offs)  ; ...the first cell is the lintel, which
        ld de,demo_objects      ; carries the colour
        add hl,de
        ld (hl),DEMO_LINTEL_TILE
        xor a
        ld (steam_on),a
        ret

; door_write -- A = tile, HL -> a list of B word offsets into the objects
door_write:
        ld c,a
dw_next:
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        push hl
        ld hl,demo_objects
        add hl,de
        ld (hl),c
        pop hl
        djnz dw_next
        ret

; ======================================================================
; object_pass -- erase or draw every object that reaches the band at
; the camera in (pass_x, pass_y).  Objects are sorted by world row and
; demo_row_ptr indexes them, so rows off the band are never touched.
; ======================================================================
object_pass:
        ld hl,(pass_y)          ; first world row that can reach the band
        ld de,7
        or a
        sbc hl,de
        jr nc,op_lo
        ld hl,0
op_lo:
        call shr3
        call row_ptr
        push hl

        ld hl,(pass_y)          ; ...and one past the last
        ld de,DEMO_BAND
        add hl,de
        call shr3
        inc l
        ld a,l
        cp DEMO_ROWS+1
        jr c,op_hi
        ld a,DEMO_ROWS
op_hi:
        ld l,a
        ld h,0
        call row_ptr
        ld (op_end),hl
        pop hl
        push hl
        pop ix                  ; IX walks the records

op_next:
        ld de,(op_end)
        push ix
        pop hl
        or a
        sbc hl,de
        ret nc                  ; reached the end of the run

        ld l,(ix+1)             ; screen X = column*4 - camera x
        ld h,0
        add hl,hl
        add hl,hl
        ld de,(pass_x)
        or a
        sbc hl,de
        ld a,h
        or a
        jr z,op_xpos
        inc a
        jr nz,op_skip           ; far off to the left
        ld a,l
        cp 253                  ; a tile is 4 bytes: -3 still shows
        jr c,op_skip
        jr op_xok
op_xpos:
        ld a,l
        cp SCR_W_BYTES
        jr nc,op_skip
op_xok:
        ld a,l
        ld (blit_x),a

        ld l,(ix+2)             ; screen Y = row*8 - camera y
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        ld de,(pass_y)
        or a
        sbc hl,de
        ld a,h
        or a
        jr z,op_ypos
        inc a
        jr nz,op_skip
        ld a,l
        cp 249                  ; ...and -7 still shows a sliver
        jr c,op_skip
        jr op_yok
op_ypos:
        ld a,l
        cp DEMO_BAND
        jr nc,op_skip
op_yok:
        ld a,l
        ld (blit_y),a
        ld a,(ix+0)
        call demo_tile
op_skip:
        ld de,3
        add ix,de
        jp op_next

shr3:                           ; HL /= 8
        srl h
        rr l
        srl h
        rr l
        srl h
        rr l
        ret

row_ptr:                        ; L = world row -> HL = first object
        ld h,0
        add hl,hl
        ld de,demo_row_ptr
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld hl,demo_objects
        add hl,de
        ret

; ======================================================================
; demo_tile -- blit one 8x8 tile, clipped to the band on all four
; edges.  A = tile index, (blit_x)/(blit_y) = screen position with the
; convention that 253..255 and 249..255 mean a few pixels off the left
; and top.  (blit_black) draws a hole instead of the tile.
; ======================================================================
demo_tile:
        push af
        ld a,(blit_y)           ; ---- vertical clip
        cp DEMO_BAND
        jr c,dt_ydown
        neg                     ; started above the band: skip rows
        ld (dt_srow),a
        ld b,a
        ld a,8
        sub b
        ld (dt_rows),a
        xor a
        ld (dt_sy),a
        jr dt_ydone
dt_ydown:
        ld (dt_sy),a
        ld b,a
        xor a
        ld (dt_srow),a
        ld a,DEMO_BAND          ; ...or runs off the bottom of it
        sub b
        cp 8
        jr c,dt_yfew
        ld a,8
dt_yfew:
        ld (dt_rows),a
dt_ydone:
        ld a,(blit_x)           ; ---- horizontal clip
        cp SCR_W_BYTES
        jr c,dt_xin
        neg
        ld (dt_scol),a
        ld b,a
        ld a,4
        sub b
        ld (dt_cols),a
        xor a
        ld (dt_sx),a
        jr dt_xdone
dt_xin:
        ld (dt_sx),a
        ld b,a
        xor a
        ld (dt_scol),a
        ld a,SCR_W_BYTES
        sub b
        cp 4
        jr c,dt_xfew
        ld a,4
dt_xfew:
        ld (dt_cols),a
dt_xdone:
        pop af                  ; ---- source: tileset + tile*32 + skip
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld de,tileset
        add hl,de
        ld a,(dt_srow)
        add a,a
        add a,a
        ld e,a
        ld a,(dt_scol)
        add a,e
        ld e,a
        ld d,0
        add hl,de
        ld a,(dt_sx)            ; ---- destination
        ld b,a
        ld a,(dt_sy)
        ld c,a
        push hl
        call screen_addr
        ex de,hl                ; DE = screen, HL = tile data
        pop hl
        ld a,(dt_rows)
        or a
        ret z
        ld b,a
dt_row:
        push bc
        push hl
        push de
        ld a,(dt_cols)
        ld c,a
        ld b,0
        ld a,(blit_black)
        or a
        jr z,dt_copy
        xor a
dt_hole:
        ld (de),a
        inc de
        dec c
        jr nz,dt_hole
        jr dt_step
dt_copy:
        ldir
dt_step:
        pop de
        pop hl
        pop bc
        ld a,l                  ; next tile row
        add a,4
        ld l,a
        jr nc,dt_src
        inc h
dt_src:
        call next_line
        djnz dt_row
        ret

; next_line -- DE += #800, with the character-row wrap the CPC needs
next_line:
        ld a,d
        add a,8
        ld d,a
        and #38
        ret nz
        ld a,e
        add a,#50
        ld e,a
        ld a,d
        adc a,#C0
        ld d,a
        ret

; ======================================================================
; The mechanic: a fixed slot near the bottom of the frame.  Only a jump
; lifts him out of it, and then only for the length of the arc.
; ======================================================================
hero_y_now:                     ; A = the screen line he is drawn on
        ld a,(seg_anim)
        cp A_JUMP
        ld a,DEMO_HERO_Y
        ret nz
        ld a,(seg_elapsed)
        cp jump_arc_len
        jr c,hy_in
        ld a,jump_arc_len-1
hy_in:
        ld e,a
        ld d,0
        ld hl,jump_arc
        add hl,de
        ld a,DEMO_HERO_Y
        add a,(hl)              ; the arc is stored as a signed offset
        ret

erase_hero:
        ld a,(buf_index)
        ld e,a
        ld d,0
        ld hl,hero_prev
        add hl,de
        ld c,(hl)
        ld b,DEMO_HERO_X
        ld e,16
        jp black_rect

draw_hero:
        ld hl,anim_ctr
        inc (hl)
        call hero_y_now
        ld c,a
        ld a,(buf_index)        ; remember the line, for the wipe two
        ld e,a                  ; updates from now
        ld d,0
        ld hl,hero_prev
        add hl,de
        ld (hl),c
        push bc
        call hero_frame         ; DE = the compiled frame to run
        pop bc
        ld b,DEMO_HERO_X
        push de
        call screen_addr
        pop de
        push de
        ret                     ; ...straight into the compiled frame

hero_frame:
        ld a,(seg_anim)
        or a
        jr nz,hf_walk
        ld de,spr_mech_climb    ; on the rails, seen from behind; the
        ld a,(beat_phase)       ; hands swap ON the beat, so each strike
        and 4                   ; is the grab of the next rung
        ret z
        inc de                  ; frame B is always the stub + 4
        inc de
        inc de
        inc de
        ret
hf_walk:
        cp A_LEFT
        ld de,spr_mech_l
        jr z,hf_step
        ld de,spr_mech_r        ; right, jumping and standing all face
        cp A_RIGHT              ; the way he is travelling
        ret nz                  ; jump/stand hold frame A
hf_step:
        ld a,(beat_phase)       ; the beat picks the leg, so while he
        and 4                   ; walks the sound IS the footfall (the
        ret z                   ; climb still steps off anim_ctr)
        inc de
        inc de
        inc de
        inc de
        ret

; ======================================================================
; The steam plume: a world-anchored sprite, on only while the script
; says the vent is blowing.  It gets its own per-buffer wipe because
; the object pass knows nothing about it.
; ======================================================================
steam_slot:                     ; HL -> this buffer's {on, x, y}
        ld a,(buf_index)
        ld e,a
        add a,a
        add a,e
        ld e,a
        ld d,0
        ld hl,steam_prev
        add hl,de
        ret

erase_steam:
        call steam_slot
        ld a,(hl)
        or a
        ret z
        ld (hl),0
        inc hl
        ld b,(hl)
        inc hl
        ld c,(hl)
        ld e,16
        jp black_rect

draw_steam:
        ld a,(steam_on)
        or a
        ret z
        ld hl,DEMO_STEAM_COL*4  ; world position -> screen
        ld de,(pass_x)
        or a
        sbc hl,de
        ld a,h
        or a
        ret nz                  ; off to one side: skip it
        ld a,l
        cp SCR_W_BYTES-4
        ret nc
        ld b,a
        push bc
        ld hl,DEMO_STEAM_ROW*8
        ld de,(pass_y)
        or a
        sbc hl,de
        pop bc
        ld a,h
        or a
        ret nz
        ld a,l
        cp DEMO_BAND-16
        ret nc
        ld c,a
        call steam_slot         ; remember it for the wipe
        ld (hl),1
        inc hl
        ld (hl),b
        inc hl
        ld (hl),c
        ld de,spr_steam_a
        ld a,(anim_ctr)
        and 4
        jr z,ds_go
        ld de,spr_steam_a+4
ds_go:
        push de
        call screen_addr
        pop de
        push de
        ret

; black_rect -- B = x byte, C = y line, E = lines; 4 bytes wide
black_rect:
        ld a,e                  ; grab the line count NOW: screen_addr
        push af                 ; trashes DE, and the EX below refills E
        call screen_addr        ; with the address's low byte
        pop af
        ex de,hl                ; DE = the screen address
        ld b,a                  ; B = lines, which is what bounds the loop
br_row:
        xor a
        ld (de),a
        inc de
        ld (de),a
        inc de
        ld (de),a
        inc de
        ld (de),a
        dec de
        dec de
        dec de
        call next_line
        djnz br_row
        ret

; ======================================================================
; The metronome, to the user's spec: every 10 frames, alternately
;
;       SOUND 1, 0, 3, 8, 1, 1, 0
;       SOUND 1, 0, 3, 8, 1, 2, 0             (ENV 1, 3, -5, 3)
;
; Tone period 0 and noise 0: NEITHER generator runs, so what sounds is
; the AY's DC step -- the channel sits at a constant level set by the
; volume, and each volume change is a soft CLICK (the digidrum trick).
; Duration 3 is exactly one envelope step: level 8 for ~30 ms, then
; zero.  With no tone there is nothing for the ENT number to shape, so
; the two SOUNDs are acoustically identical; the alternation lives on
; in the animation, swapping the walking legs and the climbing hands
; on every strike.  Everything is counted in real 50 Hz frames.
; ======================================================================
beat_tick:
        ld a,(seg_anim)         ; only movement keeps the beat: walking
        cp A_RIGHT              ; either way, or on the ladder
        jr z,bt_run
        cp A_LEFT
        jr z,bt_run
        cp A_CLIMB
        jr z,bt_run
        ld a,BEAT_FRAMES        ; standing or mid-jump: the beat rests,
        ld (beat_timer),a       ; armed for when he moves again
        ret
bt_run:
        ld hl,beat_timer
        ld a,(frames_now)
        ld b,a
        ld a,(hl)
        sub b
        jr z,bt_fire
        jr c,bt_fire
        ld (hl),a
        ret
bt_fire:
        add a,BEAT_FRAMES       ; keep the remainder, so the long-run
        ld (hl),a               ; period is exactly 15 frames
        ld hl,beat_phase
        ld a,(hl)
        xor 4                   ; the legs swap ON the beat
        ld (hl),a
bt_n:
        ld a,(sfx_timer)        ; a chirp or a ding owns the channel:
        or a                    ; the click stands aside (the legs have
        ret nz                  ; already swapped above)
        ld a,7
        ld e,#3F                ; R7: BOTH generators off -- the output
        call psg_write          ;     is the DC level, bit 6 clear
        ld a,8
        ld e,8                  ; R8: the level jumps to 8: a soft click.
        call psg_write          ;     It drops back ~30 ms later: click
        ld a,1
        ld (env_pos),a
        ret

; ======================================================================
; One-shot effects, register for register the game's own recipes:
;   JUMP: a square tone whose period shrinks each frame -- the pitch
;         RISES as he leaves the deck (period timer*16+60)
;   PING: a steady high tone (period 40), volume fading 14 -> 7
; Both count REAL frames, and both own the channel while they ring:
; the walking click stands aside, and any ringing click is cut.
; ======================================================================
sfx_start:                      ; A = SFX_*
        ld (sfx_type),a
        ld hl,sfx_len-1
        add a,l
        ld l,a
        jr nc,sx_nc
        inc h
sx_nc:
        ld a,(hl)
        ld (sfx_timer),a
        xor a
        ld (env_pos),a          ; a mid-ring click would fight the tone
        ret

sfx_update:                     ; once per update, in real frames
        ld a,(sfx_timer)
        or a
        ret z
        ld hl,frames_now
        sub (hl)
        jr nc,sx_live
        xor a
sx_live:
        ld (sfx_timer),a
        jp z,snd_off            ; spent: close the channel
        ld a,(sfx_type)
        dec a
        jr z,sfxu_jump
        ; ---- PING: period 40, fading
        xor a
        ld e,40
        call psg_write          ; R0
        ld a,1
        ld e,0
        call psg_write          ; R1
        ld a,7
        ld e,#3E                ; tone on A alone
        call psg_write
        ld a,(sfx_timer)        ; NOT a register: psg_write eats B, and
        add a,6                 ; a stale B once put 252 -- envelope
        ld e,a                  ; bit set! -- into R8
        ld a,8
        jp psg_write
sfxu_jump:
        ; ---- JUMP: the period shrinks as the timer runs out
        ld a,(sfx_timer)
        add a,a
        add a,a
        add a,a
        add a,a
        add a,60                ; 236 down to 76: the pitch climbs
        ld e,a
        xor a
        call psg_write          ; R0
        ld a,1
        ld e,0
        call psg_write          ; R1
        ld a,7
        ld e,#3E                ; tone on A alone
        call psg_write
        ld a,8
        ld e,12
        jp psg_write

sfx_len:
        defb 11,8               ; frames: JUMP, PING (the game's lengths)

; env_update -- ENV 1,3,-5,3, stepped in real frames
env_update:
        ld a,(env_pos)
        or a
        ret z                   ; nothing ringing
        ld hl,frames_now
        add a,(hl)              ; the decay is TIME, not updates
        cp env_len+1
        jr nc,env_end
        ld (env_pos),a
        ld hl,env_tab-1
        add a,l
        ld l,a
        jr nc,eu_nc
        inc h
eu_nc:
        ld e,(hl)
        ld a,8
        jp psg_write            ; just the volume: R6/R7 stay latched
env_end:
        xor a
        ld (env_pos),a
snd_off:
        ld a,8
        ld e,0
        call psg_write          ; volume off...
        ld a,7
        ld e,#3F                ; ...and the mixer closed
        jp psg_write

; duration 3 = one envelope step: the level holds ~30-40 ms, then drops
env_tab:
        defb 8,8
env_len equ 2

ay_init:
        ld a,7
        ld e,#3F                ; everything off, bit 6 clear so the
        call psg_write          ; keyboard keeps answering
        ld a,8
        ld e,0
        call psg_write
        ld a,9
        ld e,0
        call psg_write
        ld a,10
        ld e,0
        jp psg_write

; psg_write -- A = AY register, E = value.  Byte for byte the game's
; routine: that one is proven on real hardware.
psg_write:
        di
        ld b,#F4
        out (c),a               ; register number -> AY data bus
        ld bc,#F6C0
        out (c),c               ; BDIR=1 BC1=1: latch the address
        ld bc,#F600
        out (c),c               ; bus idle
        ld b,#F4
        out (c),e               ; value -> data bus
        ld bc,#F680
        out (c),c               ; BDIR=1 BC1=0: write the register
        ld bc,#F600
        out (c),c
        ei
        ret

; ======================================================================
; Screen plumbing, lifted from the game unchanged.
; ======================================================================
screen_addr:
        ld l,c
        ld h,0
        add hl,hl
        ld de,line_offsets
        add hl,de
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        ld a,(draw_page)
        or h
        ld h,a
        ld e,b
        ld d,0
        add hl,de
        ret

frame_sync:
        ld b,PPI_B_HI
fs_old:
        in a,(c)
        rra
        jr c,fs_old
fs_new:
        in a,(c)
        rra
        jr nc,fs_new
        halt
        ret

flip_buffers:
        ld a,(shown_r12)
        xor #20
        ld (shown_r12),a
        ld bc,CRTC_SEL+12
        out (c),c
        ld b,CRTC_DATA/256
        out (c),a
        ld a,(draw_page)
        xor #80
        ld (draw_page),a
        ld a,(buf_index)
        xor 1
        ld (buf_index),a
        ret

set_palette:
        ld b,GA_PORT/256
        xor a
sp_pen:
        out (c),a
        ld e,(hl)
        out (c),e
        inc hl
        inc a
        cp 16
        jr c,sp_pen
        ld a,#10
        out (c),a
        ld e,#54
        out (c),e
        ret

clear_buffers:
        ld hl,SCREEN_B
        call cb_one
        ld hl,SCREEN_A
cb_one:
        ld (hl),0
        ld d,h
        ld e,l
        inc de
        ld bc,#3FFF
        ldir
        ret

int_stub:                       ; 300 Hz in, 50 Hz out
        push af
        push hl
        ld hl,tick300
        inc (hl)
        ld a,(hl)
        cp 6
        jr c,is_out
        ld (hl),0
        ld hl,frame50
        inc (hl)
is_out:
        pop hl
        pop af
        ei
        ret
int_stub_end:

; the jump: a 30-frame parabola, as signed screen-line offsets
jump_arc:
        defb 0,-3,-6,-9,-11,-13,-15,-17,-18,-19
        defb -20,-20,-20,-20,-19,-19,-20,-20,-20,-20
        defb -19,-18,-17,-15,-13,-11,-9,-6,-3,0
jump_arc_len    equ 30

; ======================================================================
        include "demo_scene.asm"
        include "levels.asm"

; --- offset of every scanline within a 16K buffer
line_offsets:
lrow=0
        repeat 25
lline=0
        repeat 8
        defw lline*#800+lrow*80
lline=lline+1
        rend
lrow=lrow+1
        rend

; ======================================================================
shown_r12:      defb CRTC_R12_A
draw_page:      defb SCREEN_B/256
buf_index:      defb 0
tick300:        defb 0
frame50:        defb 0
last_frame:     defb 0
cam_xf:         defw 0          ; 24-bit fixed point: fraction, low, high
cam_xh:         defb 0
cam_yf:         defw 0
cam_yh:         defb 0
cam_ix:         defw 0          ; ...and the whole parts we render at
cam_iy:         defw 0
cam_prev:       defs 8,0        ; one (x, y) word pair per screen buffer
hero_prev:      defb 0,0        ; the line he was drawn on, per buffer
steam_prev:     defs 6,0        ; {on, x, y} per buffer
steam_on:       defb 0
pass_x:         defw 0
pass_y:         defw 0
blit_x:         defb 0
blit_y:         defb 0
blit_black:     defb 0
op_end:         defw 0
seg_ptr:        defw 0
seg_dx:         defw 0
seg_dy:         defw 0
seg_frames:     defb 0
seg_anim:       defb 0
seg_event:      defb 0
seg_elapsed:    defb 0
anim_ctr:       defb 0
beat_phase:     defb 0          ; bit 2: the leg, and 22 vs 26, per beat
beat_timer:     defb BEAT_FRAMES
env_pos:        defb 0          ; elapsed frames into the decay; 0 = quiet
sfx_type:       defb 0          ; 1 = jump chirp, 2 = ping
sfx_timer:      defb 0          ; frames left; 0 = free
frames_now:     defb 1          ; 50 Hz frames covered by this update
dt_srow:        defb 0
dt_scol:        defb 0
dt_rows:        defb 0
dt_cols:        defb 0
dt_sx:          defb 0
dt_sy:          defb 0
demo_end:
        assert demo_end < SCREEN_B

        include "sprites_c.asm"
