; ======================================================================
;
;   T H E   S H A F T
;   A vertical platformer for the Amstrad CPC 6128
;
;   Engine foundation: video init, double-buffered frame sync, input,
;   masked software sprites.
;
;   Assemble : rasm src/main.asm -ob build/shaft.bin
;   Disk     : iDSK build/theshaft.dsk -i build/shaft.bin -t 1 -c 1000 -e 1000
;   Run      : RUN"SHAFT
;
; ======================================================================
;
;   MEMORY MAP -- main 64K
;   -------------------------------------------------------------------
;   #0000-#003F  RST vectors. Our IM 1 interrupt stub lives at #0038
;                (copied there at init; the lower ROM is switched off
;                so the Z80 really executes our RAM bytes).
;   #0040-#0FFF  Stack. SP starts at #1000 and grows downwards.
;   #1000-#3FFF  THIS FILE: engine code, tables, sprite data (~12K).
;   #4000-#7FFF  SCREEN BUFFER B (16K).
;   #8000-#A5FF  Reserved: tile graphics, current level tilemap,
;                actor tables (filled in by the level-drawing pass).
;   #A600-#BFFF  Reserved: AMSDOS work RAM during loading, then free
;                scratch space (level unpacking buffer).
;   #C000-#FFFF  SCREEN BUFFER A (16K) -- the buffer visible at boot.
;
;   DOUBLE BUFFERING
;   The CRTC can start the display at any 16K page (register R12), so
;   we ping-pong between #C000 and #4000: draw into the hidden buffer,
;   then reprogram R12 during the frame flyback.  The swap is a single
;   register write -- no copying -- and because it happens while the
;   beam is off-screen the player can never see a half-drawn frame.
;
;   SECOND 64K (the 6128's extra bank)
;   Holds the whole shaft: compressed level maps and per-zone tile
;   sets.  Writing #C4..#C7 to the Gate Array port (#7Fxx) maps one of
;   the four extra 16K pages over #4000-#7FFF for the CPU.  Crucially
;   the video hardware ALWAYS reads the main 64K, so the trick is:
;   show buffer A (#C000), bank a page in over #4000, unpack the next
;   screen's data to #8000+, write #C0 to restore normal RAM.  The
;   player sees a stable screen the whole time.
;
; ======================================================================

; ----------------------------------------------------------------------
; Hardware constants
; ----------------------------------------------------------------------
GA_PORT         equ #7F00       ; Gate Array: mode/ROM, palette, RAM banking
CRTC_SEL        equ #BC00       ; CRTC register select
CRTC_DATA       equ #BD00       ; CRTC register write
PPI_B_HI        equ #F5         ; PPI port B high byte (bit 0 = VSYNC)

SCREEN_A        equ #C000       ; screen buffer A
SCREEN_B        equ #4000       ; screen buffer B
CRTC_R12_A      equ #30         ; R12 value that displays #C000
CRTC_R12_B      equ #10         ; R12 value that displays #4000
; R12 bits 4-5 select the 16K page:  %11 -> #C000, %01 -> #4000.
; XORing with #20 therefore flips between the two buffers.

GA_MODE0_NOROM  equ #8C         ; %10001100: mode 0, lower+upper ROM off
GA_RAM_BASE     equ #C0         ; RAM config: plain main 64K (no banking)

; ----------------------------------------------------------------------
; Input flag bits (in input_held / input_new)
; ----------------------------------------------------------------------
INP_LEFT        equ 0
INP_RIGHT       equ 1
INP_UP          equ 2           ; will become "climb ladder"
INP_DOWN        equ 3
INP_FIRE        equ 4           ; will become "jump"

; Player sprite metrics (Mode 0: 1 byte = 2 pixels)
SPR_W_BYTES     equ 4           ; 8 pixels wide
SPR_H_LINES     equ 16
SCR_W_BYTES     equ 80          ; a Mode 0 line is 80 bytes (160 pixels)
SCR_H_LINES     equ 200

; ----------------------------------------------------------------------
; Static level geometry types (see level_rects for the record format).
; The values ARE the flag bits probe_level returns, so new types
; (hazards, exits, keycard readers...) just take the next free bit.
; ----------------------------------------------------------------------
TYPE_SOLID      equ 1           ; blocks movement: walls, floors, crates
TYPE_LADDER     equ 2           ; climbable column
TYPE_DOOR       equ 4           ; security door: SOLID until opened with
                                ; a keycard (door rects carry SOLID too)

; Tile grid: the screen is 20x25 tiles of 8x8 pixels (4 bytes x 8
; lines).  Tile rows coincide exactly with the CRTC's 8-line character
; rows, which is what makes the tile blitter simple AND fast: all 8
; lines of a tile are a constant #800 apart, never crossing a row.
MAP_W           equ 20
MAP_H           equ 25
TILE_BYTES      equ 32          ; 4 bytes x 8 lines per tile

; ----------------------------------------------------------------------
; Player physics -- 8.8 fixed point (high byte = whole scanlines).
;
; Tuning: apex height = JUMP^2/(2*GRAVITY) lines, frames to apex =
; JUMP/GRAVITY.  Current numbers: launch at 2.75 lines/frame against
; 0.1875 lines/frame^2 of gravity -> ~20-line apex in ~15 frames:
; clears the two-tile crate, bonks on the platform overhead.
;
; MAX_FALL must stay BELOW 8 (one tile): the collision step relies on
; a falling body never skipping an entire slab between two frames.
; ----------------------------------------------------------------------
ST_GROUND       equ 0           ; walking on solids (or a ladder top)
ST_CLIMB        equ 1           ; on a ladder: gravity suspended
ST_AIR          equ 2           ; ballistic: jumping or falling
GRAVITY         equ #0030       ; +0.1875 lines/frame^2, pulls down
JUMP_VY         equ #FD40       ; -2.75 lines/frame at take-off
MAX_FALL        equ #0400       ; terminal velocity: 4 lines/frame
INP_VERT_MASK   equ #0C         ; (1<<INP_UP) | (1<<INP_DOWN)
INP_HORIZ_MASK  equ #03         ; (1<<INP_LEFT) | (1<<INP_RIGHT)

; ----------------------------------------------------------------------
; Entities and game state
; ----------------------------------------------------------------------
MAX_DRONES      equ 4           ; runtime slots (levels use up to 2)
; drone record layout (8 bytes):
;  +0 active  +1 x  +2 y  +3 dir (signed, +/-speed)  +4 speed
;  +5 xmin    +6 xmax     +7 animation ticker
START_LIVES     equ 3

; Sound effects (see the AY section for what each does)
SFX_JUMP        equ 1
SFX_HIT         equ 2
SFX_PING        equ 3

        org #1000

; ======================================================================
; ENTRY -- called from BASIC by RUN"SHAFT.  We never return: take the
; machine over completely (own stack, own interrupt handler, ROMs off).
; ======================================================================
start:
        di
        ld sp,#1000             ; our stack, just below the code

        ; --- Gate Array: Mode 0, and switch BOTH ROMs out of the map.
        ; With the lower ROM off, address #0038 is our RAM -- required
        ; for the IM 1 handler below.  (Writes always reach RAM on the
        ; CPC; it is only *reads* the ROM overlay intercepts.)
        ld bc,GA_PORT+GA_MODE0_NOROM
        out (c),c

        ; --- Make sure no extra-RAM bank is mapped in (plain 64K).
        ld bc,GA_PORT+GA_RAM_BASE
        out (c),c

        ; --- Install the interrupt stub at #0038 and enable IM 1.
        ; The Gate Array raises an interrupt every 52 scanlines
        ; (300 Hz).  Our stub just counts ticks: cheap, HALT-friendly,
        ; and later the natural place to drive 300 Hz music/timers.
        ld hl,int_stub
        ld de,#0038
        ld bc,int_stub_end-int_stub
        ldir
        im 1

        call set_palette
        call clear_buffers

        ; --- Video state: show buffer A, draw into buffer B.
        ld a,CRTC_R12_A
        ld (shown_r12),a
        ld bc,CRTC_SEL+12       ; select CRTC R12 (display start, high bits)
        out (c),c
        ld b,CRTC_DATA/256
        out (c),a               ; display #C000
        ld a,SCREEN_B/256
        ld (draw_page),a        ; sprite code draws into #4000

        ei

        ; --- Park every level in the second 64K, then run the menu.
        call copy_levels_to_bank
        jp menu_screen

; ======================================================================
; THE GAME LOOP -- one iteration per 1/50s frame
;
;   frame_sync   wait for the frame flyback (VBLANK)   } once per 1/50s
;   flip         show the buffer we finished drawing   } beam off-screen
;   then: input, sound, player physics, level-exit check, drones,
;   collisions, pickups, doors, and finally rendering into the buffer
;   now hidden.  Rendering starts right after the flip, so we get
;   nearly the whole frame (19,968 us) before the next flyback.
;
; Entered with JP (from the menu, a level transition or a respawn);
; those entry points reset SP, so the loop never leaks stack.
; ======================================================================
game_loop:
        call frame_sync         ; sleep until the beam flies back to the top
        call flip_buffers       ; hardware page flip -- tear-free by timing
        call read_input         ; keyboard matrix + joystick -> flag bits
        call sfx_update         ; feed the AY (one write burst per frame)
        call update_player      ; walk/climb/jump/fall state machine
        ld a,(player_y)
        cp 8                    ; crossed the top edge of the screen?
        jp c,next_level         ; up one level of the shaft
        call update_drones      ; patrol movement + animation ticker
        call check_drone_hit    ; player box vs drone boxes
        jp c,player_die
        call check_keycards     ; touching a keycard tile? collect it
        call check_doors        ; pushing a door with a card? open it
        call render_entities    ; restore tiles, draw drones + player
        call draw_hud           ; keycards (left) and lives (right)
        ld hl,frame_ctr
        inc (hl)
        jr game_loop

; ======================================================================
; frame_sync -- synchronise to the frame flyback (VBLANK)
;
; The CRTC's VSYNC output is wired to PPI port B bit 0 (port #F5xx).
; The pulse is short (~8-16 scanlines out of 312), so we busy-poll it
; rather than poll-with-HALT: a HALT only wakes every 52 lines and
; could sleep straight through the pulse.
;
; Step 1 waits for any VSYNC in progress to END -- if the game logic
; ever finishes inside the pulse itself, we must not trigger twice on
; the same flyback.
; Step 2 catches the exact moment the next pulse STARTS.
; Step 3 is the HALT: the Gate Array's interrupt counter is reset by
; VSYNC in such a way that one interrupt always fires two scanlines
; after the pulse begins.  HALTing here therefore parks the CPU at a
; deterministic spot just inside the flyback -- the perfect, always-
; safe moment to reprogram the CRTC and start drawing.
; ======================================================================
frame_sync:
        ld b,PPI_B_HI
fs_end_old:
        in a,(c)                ; read PPI port B
        rra                     ; bit 0 (VSYNC) -> carry
        jr c,fs_end_old         ; still inside a pulse? wait it out
fs_wait_new:
        in a,(c)
        rra
        jr nc,fs_wait_new       ; wait for the new pulse to begin
        halt                    ; ride to the VSYNC-locked interrupt
        ret

; ======================================================================
; flip_buffers -- hardware page flip + swap of all per-buffer state
;
; Called immediately after frame_sync, i.e. while the beam is safely
; off-screen.  One CRTC write makes the buffer we just finished
; visible; from now on we draw into the other one.
; ======================================================================
flip_buffers:
        ld a,(shown_r12)
        xor #20                 ; #30 <-> #10 : the other 16K page
        ld (shown_r12),a
        ld bc,CRTC_SEL+12
        out (c),c               ; select R12 (display start address, high)
        ld b,CRTC_DATA/256
        out (c),a               ; new base takes effect this frame

        ld a,(draw_page)
        xor #80                 ; #40 <-> #C0 : draw into the hidden buffer
        ld (draw_page),a

        ld a,(buf_index)        ; 0/1: which "previous position" slot the
        xor 1                   ; erase code should use for this buffer
        ld (buf_index),a
        ret

; ======================================================================
; read_input -- scan the keyboard matrix, merge keys and joystick into
; one flags byte:
;
;   input_held : bit0=Left  bit1=Right  bit2=Up  bit3=Down  bit4=Fire
;                (1 = currently held)
;   input_new  : same bits, but only the ones that turned on THIS
;                frame -- exactly what jump logic wants (press, not hold)
;
; Keys: cursor keys + Space.  Joystick 0 works automatically because
; it is simply wired into the keyboard matrix as line 9.
;
; --- How the CPC keyboard works ---------------------------------------
; The 10x8 key matrix hangs off the AY-3-8912 sound chip's I/O port A,
; and the AY itself is only reachable through the 8255 PPI:
;
;   PPI port A (#F4xx) = data bus to the AY
;   PPI port C (#F6xx) = bits 7-6 drive the AY's BDIR/BC1 control
;                        lines, bits 3-0 select the matrix row
;   PPI ctrl   (#F7xx) = direction setup for ports A/C
;
; Sequence: tell the AY we want its register 14 (I/O port A), then for
; each matrix row 0-9: put "read + row number" on port C and read the
; 8 column bits back through port A.  Bits are ACTIVE LOW (0=pressed).
; ======================================================================
read_input:
        call scan_keyboard      ; fill key_matrix[0..9]

        ld c,0                  ; C collects the "held" flags

        ld a,(key_matrix+9)     ; line 9 = joystick 0
        cpl                     ; invert once: now 1 = pressed
        ld b,a                  ; B: bit0=Up 1=Down 2=Left 3=Right 4=F2 5=F1

        ; ---- LEFT : joystick left, or cursor-left (line 1, bit 0)
        ld a,(key_matrix+1)
        cpl
        rrca                    ; cursor-left -> carry
        jr c,ri_left
        bit 2,b                 ; joystick left?
        jr z,ri_no_left
ri_left:
        set INP_LEFT,c
ri_no_left:

        ld a,(key_matrix+0)     ; line 0 holds all three other cursors
        cpl
        ld e,a                  ; E: bit0=Up 1=Right 2=Down (inverted)

        ; ---- RIGHT : joystick right, or cursor-right (line 0, bit 1)
        bit 1,e
        jr nz,ri_right
        bit 3,b
        jr z,ri_no_right
ri_right:
        set INP_RIGHT,c
ri_no_right:

        ; ---- UP : joystick up, or cursor-up (line 0, bit 0)
        bit 0,e
        jr nz,ri_up
        bit 0,b
        jr z,ri_no_up
ri_up:
        set INP_UP,c
ri_no_up:

        ; ---- DOWN : joystick down, or cursor-down (line 0, bit 2)
        bit 2,e
        jr nz,ri_down
        bit 1,b
        jr z,ri_no_down
ri_down:
        set INP_DOWN,c
ri_no_down:

        ; ---- FIRE : joystick fire 1 or 2, or Space (line 5, bit 7)
        ld a,(key_matrix+5)
        cpl
        rlca                    ; Space -> carry
        jr c,ri_fire
        bit 5,b                 ; fire 1
        jr nz,ri_fire
        bit 4,b                 ; fire 2
        jr z,ri_no_fire
ri_fire:
        set INP_FIRE,c
ri_no_fire:

        ; ---- edge detection: new = now AND NOT before
        ld hl,input_held
        ld a,(hl)
        cpl
        and c
        ld (input_new),a
        ld (hl),c
        ret

; ----------------------------------------------------------------------
; scan_keyboard -- read all 10 matrix rows into key_matrix (active low)
;
; This is the classic fast scan: ~10 port reads for the whole keyboard.
; DI while we drive the PPI so nothing can interleave with the AY
; handshake (our interrupt stub doesn't touch the PPI, but staying
; atomic keeps this routine drop-in safe whatever runs on the ints).
; ----------------------------------------------------------------------
scan_keyboard:
        di
        ld hl,key_matrix
        ld bc,#F782             ; PPI control: port A output, port B input,
        out (c),c               ;   port C output (mode 0)
        ld bc,#F40E             ; port A = 14 = AY register "I/O port A"
        ld e,b                  ; E = #F4, kept for the loop below
        out (c),c
        ld bc,#F6C0             ; port C = %11xxxxxx : BDIR=1 BC1=1
        ld d,b                  ; D = #F6              = latch reg address
        out (c),c
        ld c,0
        out (c),c               ; AY bus back to inactive
        ld bc,#F792             ; PPI control: port A now an INPUT
        out (c),c

        ld a,#40                ; %01 on BDIR/BC1 = "read AY reg", row 0
        ld c,#4A                ; loop stop value (row 10)
sk_row:
        ld b,d                  ; B = #F6 (PPI port C)
        out (c),a               ; select row + AY read mode
        ld b,e                  ; B = #F4 (PPI port A)
        ini                     ; (HL) <- column bits, HL++, B trashed
        inc a
        cp c
        jr c,sk_row             ; rows #40..#49 = matrix lines 0..9

        ld bc,#F782             ; PPI port A back to output (default state)
        out (c),c
        ei
        ret

; ======================================================================
; update_player -- the player physics state machine
;
;   GROUND  walking on something solid (or a ladder top).  Can walk,
;           jump (Fire, on the press edge), mount ladders (Up/Down),
;           or walk off an edge and start to fall.
;   CLIMB   hanging on a ladder: gravity suspended.  Up/Down climb
;           (same find_ladder discipline as mounting, so the end-stops
;           still come from data), Left/Right step off, Fire leaps off.
;   AIR     ballistic.  Every frame: vy += GRAVITY (capped at
;           MAX_FALL), position += vy.  Left/Right steer (air
;           control), no double jumps, and holding Up/Down catches a
;           ladder on the way past.
;
; Vertical position is 8.8 fixed point: player_y stays the visible
; whole scanline exactly as before, player_yfrac holds the 1/256ths.
; The two bytes are adjacent, so LD HL,(player_yfrac) reads the whole
; position and one store commits both.
;
; Landing and head-bump snapping lean on a level invariant: every
; solid's top and bottom edge is tile-aligned (multiple of 8) because
; the rects are compiled from the tile map.  With MAX_FALL below one
; tile, the surface a falling body crossed this frame is simply the
; multiple of 8 its feet just passed -- snap there, done.  The snap
; also keeps y EVEN, which the ladder code's 2-line steps require.
;
; Every move is still TRY-THEN-COMMIT via the Prompt 2 probes: the
; player is never inside a wall, so nothing needs pushing back out.
; ======================================================================
update_player:
        ld a,(player_state)
        or a
        jr z,upd_ground
        dec a                   ; ST_CLIMB?
        jr z,upd_climb
        jp upd_air

; --------------------------- GROUNDED ---------------------------------
upd_ground:
        ld a,(input_new)        ; Fire = jump, on the PRESS edge only
        bit INP_FIRE,a
        jr z,ug_no_jump
        call start_jump         ; airborne with full upward velocity...
        jp move_horizontal      ; ...and may steer this same frame
ug_no_jump:
        ld a,(input_held)       ; Up/Down: try to mount a ladder
        bit INP_UP,a
        jr z,ug_no_up
        ld a,(player_y)
        sub 2
        ld c,a
        call try_mount
        ret c                   ; mounted: climbing consumed the frame
ug_no_up:
        ld a,(input_held)
        bit INP_DOWN,a
        jr z,ug_no_down
        ld a,(player_y)
        add a,2
        ld c,a
        call try_mount
        ret c
ug_no_down:
        call move_horizontal    ; walk...
        call is_supported       ; ...then is the ground still there?
        or a
        ret nz                  ; something real underfoot: stay put
        jp start_fall           ; walked off an edge: drop from rest

; --------------------------- CLIMBING ---------------------------------
upd_climb:
        ld a,(input_new)        ; Fire: leap off the ladder
        bit INP_FIRE,a
        jr z,uc_no_jump
        call start_jump
        jp move_horizontal
uc_no_jump:
        ld a,(input_held)       ; Up/Down: climb at 2 lines/frame
        bit INP_UP,a
        jr z,uc_no_up
        ld a,(player_y)
        sub 2
        ld c,a
        call climb_step
uc_no_up:
        ld a,(input_held)
        bit INP_DOWN,a
        jr z,uc_no_down
        ld a,(player_y)
        add a,2
        ld c,a
        call climb_step
uc_no_down:
        call move_horizontal    ; Left/Right: try to step off sideways
        ld a,e                  ; E=1 if a step was committed
        or a
        ret z                   ; still holding the rails
        call is_supported       ; stepped off: onto ground, or into air?
        or a
        jp z,start_fall
        ld a,ST_GROUND
        ld (player_state),a
        ret

; --------------------------- AIRBORNE ---------------------------------
upd_air:
        call move_horizontal    ; air control
        ; ---- gravity, with terminal velocity
        ld hl,(player_vy)
        ld de,GRAVITY
        add hl,de
        bit 7,h                 ; still moving upwards? then no cap
        jr nz,ua_vy_ok
        ld a,h
        cp MAX_FALL/256
        jr c,ua_vy_ok
        ld hl,MAX_FALL
ua_vy_ok:
        ld (player_vy),hl
        ; ---- integrate: one 16-bit add moves fraction and line
        ex de,hl                ; DE = vy
        ld hl,(player_yfrac)    ; L = fraction, H = whole line
        ld b,h                  ; B = the line we are leaving
        add hl,de
        ld a,h
        cp b                    ; did the whole line change?
        jr z,ua_store           ; no: just keep the new fraction
        jr c,ua_rising
        ; ---- FALLING: would the body end up inside something solid?
        ld c,a
        push hl
        call solid_at_y
        pop hl
        jr nc,ua_store          ; clear air: commit the move
        ; Landed.  Snap the feet onto the tile-aligned surface they
        ; crossed this frame and go back to walking.
        ld a,h
        add a,SPR_H_LINES       ; feet line after the move...
        and #F8                 ; ...the surface is the multiple of 8
        sub SPR_H_LINES         ;    just above them.  Stand exactly on
        ld (player_y),a         ;    it (y comes out even, too)
        xor a
        ld (player_yfrac),a
        ld hl,0
        ld (player_vy),hl
        ld a,ST_GROUND
        ld (player_state),a
        ; fall-height hook (future: damage, landing thud, dust puff)
        ld a,(fall_start)
        ld b,a
        ld a,(player_y)
        sub b                   ; lines dropped since the arc's apex
        jr nc,ua_keep_fall
        xor a                   ; landed above the apex? then no fall
ua_keep_fall:
        ld (last_fall),a
        ret
ua_rising:
        ; ---- RISING: head into a slab?
        ld c,a
        push hl
        call solid_at_y
        pop hl
        jr nc,ua_store          ; clear: commit
        ld a,h                  ; bonk.  Park exactly under the ceiling
        and #F8
        add a,8                 ; = first free line below the slab
        ld (player_y),a
        xor a
        ld (player_yfrac),a
        ld hl,0                 ; upward speed dies here; gravity brings
        ld (player_vy),hl       ; us back down over the next frames
        ret
ua_store:
        ld (player_yfrac),hl    ; one store commits fraction AND line
        ld a,h                  ; track the highest point of the arc:
        ld hl,fall_start        ; fall distance is measured from the
        cp (hl)                 ; apex, not from the take-off point
        jr nc,ua_grab
        ld (hl),a
ua_grab:
        ; ---- catch a ladder in flight: hold Up/Down while lined up
        ld a,(input_held)
        and INP_VERT_MASK
        ret z
        ld a,(player_y)
        and #FE                 ; climbing works in 2-line steps: round
        ld c,a                  ; to even before the containment test
        ld a,(player_x)
        ld b,a
        call find_ladder
        ret nc
        ld (player_x),a         ; caught the rails
        ld a,c
        ld (player_y),a
        xor a
        ld (player_yfrac),a
        ld hl,0
        ld (player_vy),hl
        ld a,ST_CLIMB
        ld (player_state),a
        ret

; ----------------------------------------------------------------------
; move_horizontal -- shared walk/steer: one byte (2 px) left/right per
; frame, cancelled if the destination box overlaps a solid.
; Out: E = 1 if a step was committed (the climb code wants to know).
; ----------------------------------------------------------------------
move_horizontal:
        ld e,0
        ld a,(input_held)
        bit INP_LEFT,a
        jr z,mh_no_left
        ld a,(player_x)
        or a                    ; screen-edge guard
        jr z,mh_no_left
        dec a
        call solid_at_x         ; carry = a solid is in the way
        jr c,mh_no_left
        ld (player_x),a
        ld e,1
mh_no_left:
        ld a,(input_held)
        bit INP_RIGHT,a
        jr z,mh_no_right
        ld a,(player_x)
        cp SCR_W_BYTES-SPR_W_BYTES
        jr nc,mh_no_right
        inc a
        call solid_at_x
        jr c,mh_no_right
        ld (player_x),a
        ld e,1
mh_no_right:
        ret

; ----------------------------------------------------------------------
; Probe helpers -- build a box for probe_level, reduce the answer to
; something branchable.
; ----------------------------------------------------------------------
; solid_at_x: A = candidate x -> carry set if the body would overlap a
; solid there.  A and E survive (A comes back for the commit).
solid_at_x:
        push de
        ld b,a
        ld a,(player_y)
        ld c,a
        ld d,SPR_W_BYTES
        ld e,SPR_H_LINES
        call probe_level        ; preserves BC (so B still holds x)
        rra                     ; TYPE_SOLID bit -> carry
        ld a,b
        pop de
        ret

; solid_at_y: C = candidate y -> carry set if the body would overlap a
; solid there (x unchanged).
solid_at_y:
        push de
        ld a,(player_x)
        ld b,a
        ld d,SPR_W_BYTES
        ld e,SPR_H_LINES
        call probe_level
        rra
        pop de
        ret

; support_at: A = probe flags of the 1-line strip directly under the
; feet -- this is why probe_level takes an arbitrary box.
support_at:
        ld a,(player_x)
        ld b,a
        ld a,(player_y)
        add a,SPR_H_LINES
        ld c,a
        ld d,SPR_W_BYTES
        ld e,1
        jp probe_level

; ----------------------------------------------------------------------
; is_supported -- is there something to STAND on directly underfoot?
; Out: A nonzero if supported (test with OR A).
;
; Support is a SOLID in the strip under the feet, or standing exactly
; at a ladder's through-hole.  The subtlety: the raw LADDER bit must
; NOT count as support, or hanging in mid-air beside a ladder column
; would read as ground (the column spans every row it passes).  The
; head-room convention identifies the real standing spot: a ladder
; rect starts 16 lines above the surface it pierces, so "feet ==
; ladder.y+16" IS that surface's hole -- and nothing else is.
; ----------------------------------------------------------------------
is_supported:
        call support_at
        and TYPE_SOLID
        ret nz                  ; solid underfoot: done
        ld a,(player_y)
        add a,SPR_H_LINES
        ld c,a                  ; C = feet line
        ld a,(player_x)
        ld b,a                  ; B = player x
        ld ix,(current_rects)
is_next:
        ld a,(ix+0)
        cp #FF
        jr z,is_none
        ld a,(ix+4)
        cp TYPE_LADDER
        jr nz,is_skip
        ld a,(ix+2)             ; ladder.y ...
        add a,SPR_H_LINES       ; ... +16 = its standing level
        cp c
        jr nz,is_skip           ; feet are not at that level
        ld a,b                  ; x-spans touch iff |px - lx| <= 3
        sub (ix+0)
        add a,3
        cp 7
        jr nc,is_skip
        ld a,1                  ; standing in the through-hole
        ret
is_skip:
        repeat 5
        inc ix
        rend
        jr is_next
is_none:
        xor a
        ret

; ----------------------------------------------------------------------
; climb_step: C = destination y.  Commits the move (with rail snap) if
; find_ladder approves; carry mirrors success.
; try_mount:  the same, but also enters the CLIMB state -- used when
; stepping onto a ladder from the ground.
; ----------------------------------------------------------------------
try_mount:
        call climb_step
        ret nc
        ld a,ST_CLIMB
        ld (player_state),a     ; (loads don't touch carry: still set)
        ret
climb_step:
        ld a,(player_x)
        ld b,a
        call find_ladder        ; carry = approved, A = the ladder's x
        ret nc
        ld (player_x),a         ; mount assist: snap onto the rails
        ld a,c
        ld (player_y),a
        ret

; ----------------------------------------------------------------------
; start_jump / start_fall -- enter the AIR state: from a Fire press,
; or from losing the ground under our feet.
; ----------------------------------------------------------------------
start_fall:
        ld hl,0                 ; drop from rest
        jr sj_common
start_jump:
        ld a,SFX_JUMP           ; rising tone as we leave the ground
        call sfx_start
        ld hl,JUMP_VY           ; leap: full upward velocity at once
sj_common:
        ld (player_vy),hl
        xor a
        ld (player_yfrac),a
        ld a,(player_y)
        ld (fall_start),a       ; the apex tracker starts here
        ld a,ST_AIR
        ld (player_state),a
        ret

; ======================================================================
; erase_player / draw_player -- double-buffer-aware sprite management
;
; With two buffers, the image to wipe is the one drawn into THIS
; buffer TWO frames ago -- so we keep one (x,y) slot per buffer and
; buf_index (toggled by the flip) picks the right one.
; ======================================================================
erase_player:
        call prev_slot          ; HL -> this buffer's (x,y) slot
        ld b,(hl)               ; B = old X
        inc hl
        ld c,(hl)               ; C = old Y
        jp restore_tiles        ; re-blit the map tiles under that block

draw_player:
        call prev_slot
        ld a,(player_x)
        ld (hl),a               ; remember where we draw (for the wipe
        ld b,a                  ;  two frames from now)
        inc hl
        ld a,(player_y)
        ld (hl),a
        ld c,a
        ld de,spr_mechanic
        jp draw_sprite_8x16

prev_slot:                      ; HL = prev_pos + 2*buf_index
        ld hl,prev_pos
        ld a,(buf_index)
        add a,a
        ld c,a
        ld b,0
        add hl,bc
        ret

; ======================================================================
; screen_addr -- screen address of byte column B, scanline C, in the
;                current DRAW buffer
;
; In : B = X in bytes (0..79),  C = Y in lines (0..199)
; Out: HL = screen address        (B, C preserved; A, DE trashed)
;
; CPC screen layout is interleaved: consecutive scanlines are #800
; bytes apart, and every 8th line starts a new 80-byte character row.
; Rather than multiply every time, we look the line offset up in
; line_offsets (200 words, generated at assembly time) and OR in the
; buffer's base page -- possible because both bases (#4000/#C000) are
; 16K-aligned and offsets never exceed 16K.
; ======================================================================
screen_addr:
        ld l,c
        ld h,0
        add hl,hl               ; Y*2 -> word index
        ld de,line_offsets
        add hl,de
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a                  ; HL = offset of line start (0..#3FFF)
        ld a,(draw_page)        ; #40 or #C0
        or h
        ld h,a                  ; + buffer base
        ld e,b
        ld d,0
        add hl,de               ; + X byte column
        ret

; ======================================================================
; draw_sprite_8x16 -- masked 8x16-pixel software sprite (Mode 0)
;
; In : B  = X in BYTES (0..76) -- Mode 0 packs 2 pixels per byte, so
;           horizontal movement is in 2-pixel steps (fine for chunky
;           arcade sprites; sub-byte X needs 2 pre-shifted copies)
;      C  = Y in LINES (0..184)
;      DE = sprite data: 16 lines x 4 columns of (MASK,DATA) pairs
;
; The mask has 1-bits where the BACKGROUND survives and the data has
; 0-bits there, so each byte is composited with:
;
;       screen = (screen AND mask) OR data
;
; That is what makes the sprite "masked": it punches a hole in the
; background instead of erasing a rectangle around itself.
;
; The inner row is unrolled (4 bytes, no loop overhead) and stepping
; to the next scanline is the classic CPC "+#800 with character-row
; wrap" sequence -- see the comment at fs/next-line below.
; Total cost ~4,600 T-states =~ 6% of a frame.  Plenty of headroom;
; when the screen fills with drones we can graduate to compiled
; sprites or stack-pointer tricks.
; ======================================================================
draw_sprite_8x16:
        push de
        call screen_addr        ; HL = top-left byte on screen
        pop de
        ld c,SPR_H_LINES        ; 16 rows
ds_row:
        push hl                 ; keep the row's start address
        repeat 4                ; ---- one screen byte, unrolled x4 ----
        ld a,(de)               ; mask
        and (hl)                ; keep background where mask=1
        ld b,a
        inc de
        ld a,(de)               ; pixel data
        or b                    ; merge sprite pixels in
        ld (hl),a
        inc de
        inc hl
        rend                    ; --------------------------------------
        pop hl

        ; ---- step HL to the next scanline ----
        ; +#800 moves down one line inside an 8-line character row.
        ; If that wrapped the line bits (H bits 3-5) to zero we fell
        ; off the row: add #C050 (= -#4000 + #50) to land on the first
        ; line of the next 80-byte character row.  Works for both
        ; buffer bases because they are 16K-aligned.
        ld a,h
        add a,8
        ld h,a
        and #38
        jr nz,ds_same_row
        ld a,l
        add a,#50
        ld l,a
        ld a,h
        adc a,#C0
        ld h,a
ds_same_row:
        dec c
        jr nz,ds_row
        ret

; ======================================================================
;
;   COLLISION
;
; Level geometry is a list of axis-aligned rectangles (level_rects):
; cheap to test, trivial to author, and exactly what the tilemap of
; Prompt 3 will compile itself down to.  All coordinates use the same
; units as the player: X in Mode 0 bytes, Y in scanlines.
;
; ======================================================================

; ----------------------------------------------------------------------
; box_overlap -- the fundamental AABB (axis-aligned bounding box) test
;
; In : IX -> a rect record       {x, w, y, h}  (type byte follows at +4)
;      B=x  C=y  D=w  E=h        the moving box (usually the player)
; Out: CARRY SET if the two boxes overlap, clear if not.
;      A destroyed; B,C,D,E,IX all preserved -- so callers can walk a
;      list and test many rects without reloading anything.
;
; Two boxes overlap iff they overlap on BOTH axes.  Per axis we test
; the two "gap" cases and bail on the first miss (cheapest exit for
; the common case, since most rects are nowhere near the player):
;
;      no overlap when   box right edge  <  rect left edge
;                  or    rect right edge <  box left edge
;
; Edges are EXCLUSIVE: a sprite standing ON a platform (its bottom
; touching the slab's top) does NOT overlap it.  That is exactly what
; platform logic wants -- "resting on" must not read as "stuck in".
; All sums fit in 8 bits by construction (x+w <= 80, y+h <= 200), so
; every compare below is a plain unsigned CP.
; ----------------------------------------------------------------------
box_overlap:
        ; --- X axis ---
        ld a,b
        add a,d
        dec a                   ; A = box's rightmost occupied column
        cp (ix+0)               ; < rect.x ?  then a gap lies between
        jr c,bo_miss
        ld a,(ix+0)
        add a,(ix+1)
        dec a                   ; A = rect's rightmost column
        cp b                    ; < box.x ?  gap the other way round
        jr c,bo_miss
        ; --- Y axis, same idea ---
        ld a,c
        add a,e
        dec a                   ; box's bottom line
        cp (ix+2)
        jr c,bo_miss
        ld a,(ix+2)
        add a,(ix+3)
        dec a                   ; rect's bottom line
        cp c
        jr c,bo_miss
        scf                     ; overlap on both axes: HIT
        ret
bo_miss:
        or a                    ; clear carry: miss
        ret

; ----------------------------------------------------------------------
; probe_level -- test a box against EVERY static rect on this screen
;
; In : B=x  C=y  D=w  E=h   (the probe box)
; Out: A = OR of the type bits of everything overlapped:
;          bit 0 (TYPE_SOLID)   wall / floor / platform / crate
;          bit 1 (TYPE_LADDER)  ladder
;      A=0 means clear air.  BC,DE preserved; HL,IX trashed.
;
; The probe box does NOT have to be the whole sprite: the physics pass
; will call this with thin probes too ("is there floor under my feet",
; "is my head hitting a slab"), which is why the box is parameterised.
; ----------------------------------------------------------------------
probe_level:
        ld ix,(current_rects)   ; via pointer: screen changes just re-aim it
        ld l,0                  ; L accumulates the type bits
pl_next:
        ld a,(ix+0)
        cp #FF                  ; x=#FF terminates the list
        jr z,pl_done
        call box_overlap
        jr nc,pl_skip
        ld a,(ix+4)             ; overlapped: OR this rect's type in
        or l
        ld l,a
pl_skip:
        repeat 5                ; advance to the next 5-byte record
        inc ix                  ; (can't ADD IX,BC -- BC is the probe)
        rend
        jr pl_next
pl_done:
        ld a,l
        ret

; ----------------------------------------------------------------------
; find_ladder -- can the player occupy destination Y on some ladder?
;
; In : B = player x (bytes),  C = DESTINATION y (lines)
; Out: Carry set   = yes; A = that ladder's x (caller snaps to it)
;      Carry clear = no ladder there / would leave its span
;      B,C,E preserved; D,HL,IX trashed
;
; Why isn't this just box_overlap?  Because ANY overlap would let you
; keep climbing while any part of your body still brushes the ladder --
; you could rise until your head pokes 14 lines past the top, or grab
; a ladder you are barely touching sideways.  Climbing wants two
; stricter rules:
;
;  1. ALIGNMENT: |player_x - ladder_x| <= 1 byte.  Close enough to
;     grab; update_player then snaps X onto the rails, so the sprite
;     always renders cleanly inside the platform gap.
;  2. CONTAINMENT: the whole 16-line body must fit inside the ladder's
;     CLIMB VOLUME:   ladder.y <= y  and  y+16 <= ladder.y + ladder.h.
;     A ladder rect therefore spans from (upper_floor_y - 16), i.e.
;     including the head-room above the slab, down to the lower floor.
;     The climb then stops, by data alone, exactly when the feet reach
;     either floor -- no special "top of ladder" code at all.
;
; Note the climb step is 2 lines, so keep every platform surface at an
; EVEN y in level_rects or the end-stops can be stepped over.
; ----------------------------------------------------------------------
find_ladder:
        ld ix,(current_rects)
fl_next:
        ld a,(ix+0)
        cp #FF
        jr z,fl_none
        ld a,(ix+4)
        cp TYPE_LADDER          ; only ladders interest us here
        jr nz,fl_skip
        ; --- rule 1: alignment within one byte
        ld a,b
        sub (ix+0)              ; player_x - ladder_x
        inc a                   ; -1..+1  ->  0..2
        cp 3
        jr nc,fl_skip           ; too far sideways to grab
        ; --- rule 2: body inside the climb volume
        ld a,c
        cp (ix+2)               ; destination above the ladder top?
        jr c,fl_skip
        add a,SPR_H_LINES       ; A = destination bottom (exclusive)
        ld d,a
        ld a,(ix+2)
        add a,(ix+3)            ; A = ladder bottom (exclusive)
        cp d                    ; feet would poke out below the end?
        jr c,fl_skip
        ld a,(ix+0)             ; approved -- hand back the snap target
        scf
        ret
fl_skip:
        repeat 5
        inc ix
        rend
        jr fl_next
fl_none:
        or a
        ret

; ======================================================================
;
;   TILE RENDERER
;
; The level background is a 20x25 grid of 8x8-pixel tiles: 500 map
; bytes describe a screen, each indexing a 32-byte tile (4 bytes x
; 8 lines of raw Mode 0 pixels -- tiles are opaque, no masks).
;
; Everything is read through pointers (current_map, current_tileset,
; current_rects), so the flip-screen loader of a later pass switches
; screens by re-aiming three words -- the renderer never changes.
; The map, tiles and collision rects are all generated from one ASCII
; source by tools/level_gen.py.
;
; ======================================================================

; ----------------------------------------------------------------------
; draw_tilemap -- render the whole 20x25 map into the DRAW buffer
;
; Costs ~7 frames; used at level entry / screen transitions only.
; (If transitions ever need to be snappier: render one buffer and
; LDIR it into the other, or switch draw_tile to a stack-abusing
; compiled-tile scheme.  No need yet.)
; ----------------------------------------------------------------------
draw_tilemap:
        ld e,0                  ; E = tile row
dm_row:
        ld d,0                  ; D = tile column
dm_col:
        call draw_map_tile      ; preserves D,E
        inc d
        ld a,d
        cp MAP_W
        jr c,dm_col
        inc e
        ld a,e
        cp MAP_H
        jr c,dm_row
        ret

; ----------------------------------------------------------------------
; draw_map_tile -- look up the map and blit ONE tile cell
;
; In : D = tile column (0..19), E = tile row (0..24)
; Out: D,E preserved; everything else trashed.
; Silently clips if D/E are off the map, so callers can over-scan the
; sprite neighbourhood without edge special-cases.
; ----------------------------------------------------------------------
draw_map_tile:
        ld a,d
        cp MAP_W
        ret nc                  ; off the right edge: nothing to do
        ld a,e
        cp MAP_H
        ret nc                  ; off the bottom
        push de
        call map_cell_addr      ; HL -> the map byte for (D,E)
        ld a,(hl)               ; A = tile index
        push af
        ; --- screen address of the cell: x = col*4 bytes, y = row*8
        ld a,d
        add a,a
        add a,a
        ld b,a                  ; B = x byte
        ld a,e
        add a,a
        add a,a
        add a,a
        ld c,a                  ; C = scanline
        call screen_addr        ; HL = destination (trashes A,DE)
        ex de,hl                ; draw_tile wants the dest in DE
        pop af
        call draw_tile
        pop de
        ret

; ----------------------------------------------------------------------
; map_cell_addr -- HL = address of map cell (D=column, E=row) in the
; CURRENT map: (current_map) + row*20 + column.  Trashes A,BC.
; The map lives in a main-RAM buffer, so callers may WRITE through the
; returned pointer -- that is how keycards vanish and doors open.
; ----------------------------------------------------------------------
map_cell_addr:
        ld l,e
        ld h,0
        add hl,hl               ; row*2
        add hl,hl               ; row*4
        ld b,h
        ld c,l
        add hl,hl               ; row*8
        add hl,hl               ; row*16
        add hl,bc               ; row*20
        ld c,d
        ld b,0
        add hl,bc               ; + column
        ld bc,(current_map)
        add hl,bc
        ret

; ----------------------------------------------------------------------
; redraw_cell_both -- re-blit map cell (D,E) into BOTH screen buffers.
; Used when the map itself changes (keycard taken, door opened): the
; change must appear in the visible buffer AND the hidden one.
; ----------------------------------------------------------------------
redraw_cell_both:
        ld a,(draw_page)
        push af
        ld a,SCREEN_B/256
        ld (draw_page),a
        call draw_map_tile      ; preserves D,E
        ld a,SCREEN_A/256
        ld (draw_page),a
        call draw_map_tile
        pop af
        ld (draw_page),a
        ret

; ----------------------------------------------------------------------
; draw_tile -- blit one 8x8 tile: the innermost hot loop
;
; In : A  = tile index (0..255)
;      DE = screen address of the cell's top-left byte.  MUST be on a
;           character-row boundary (y multiple of 8): that guarantee is
;           what lets every line step be a constant +#800 with no
;           row-crossing test (compare the sprite routine, which can't
;           assume alignment and pays for the wrap check every line).
; Out: A,BC,HL,DE trashed.
;
; The row copy is LDI-unrolled: 4 bytes x 8 lines = 32 LDIs, with a
; 6-instruction "down one line, back 4 bytes" (+#7FC) seam between
; lines.  ~700 T-states per tile.
; ----------------------------------------------------------------------
draw_tile:
        ld l,a                  ; HL = (current_tileset) + index*32
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld bc,(current_tileset)
        add hl,bc
        repeat 7
        ldi                     ; one 4-byte tile line...
        ldi
        ldi
        ldi
        ld a,e                  ; ...then DE += #800-4: next scanline,
        add a,#FC               ;    same column (no wrap possible
        ld e,a                  ;    inside an aligned character row)
        ld a,d
        adc a,#07
        ld d,a
        rend
        ldi                     ; 8th line: no seam needed after it
        ldi
        ldi
        ldi
        ret

; ----------------------------------------------------------------------
; restore_tiles -- repaint the background under a sprite's old image
;
; In : B = x byte, C = y line of an 8x16-pixel block.
; The block can straddle at most 2 tile columns and 3 tile rows; we
; simply re-blit that whole neighbourhood from the map (~6 tiles,
; ~4.5% of a frame).  This replaces the old black-box erase AND the
; ladder-repair special case: with a tile background, restoring is
; just drawing the truth again.
; ----------------------------------------------------------------------
restore_tiles:
        ld a,b                  ; D = leftmost tile column = x/4
        srl a
        srl a
        ld d,a
        ld a,c                  ; E = topmost tile row = y/8
        srl a
        srl a
        srl a
        ld e,a
        ; Walk the 2x3 neighbourhood in a zig-zag so D/E themselves do
        ; the bookkeeping; draw_map_tile preserves them and clips the
        ; cells that fall off the map's right/bottom edge.
        call draw_map_tile      ; (D  , E  )
        inc e
        call draw_map_tile      ; (D  , E+1)
        inc e
        call draw_map_tile      ; (D  , E+2)
        inc d
        call draw_map_tile      ; (D+1, E+2)
        dec e
        call draw_map_tile      ; (D+1, E+1)
        dec e
        jp draw_map_tile        ; (D+1, E  ) -- tail call

; ----------------------------------------------------------------------
; fill_rect -- flood a rectangle of screen bytes with one value
; In : B=x (bytes)  C=y (lines)  D=w (>=1)  E=h (>=1)
;      A = fill byte (normally the same pen in both Mode 0 pixels)
; Out: D preserved, everything else working-trashed.  Draw buffer only.
; No longer part of level rendering -- kept for HUD bars, screen wipes
; and debug overlays.
; ----------------------------------------------------------------------
fill_rect:
        push af
        push de
        call screen_addr        ; HL = top-left corner (needs B,C; eats DE)
        pop de
        pop af
        ld c,a                  ; C = fill byte (Y is baked into HL now)
fr_line:
        push hl
        ld b,d                  ; W bytes across...
fr_byte:
        ld (hl),c
        inc hl
        djnz fr_byte
        pop hl
        ld a,h                  ; ...then the usual next-scanline step
        add a,8
        ld h,a
        and #38
        jr nz,fr_same
        ld a,l
        add a,#50
        ld l,a
        ld a,h
        adc a,#C0
        ld h,a
fr_same:
        dec e
        jr nz,fr_line
        ret

; ======================================================================
;
;   LEVELS AND THE SECOND 64K
;
; The 6128's extra 64K is four 16K pages selected by the Gate Array:
; writing %110001bb to port #7Fxx maps extra page bb over CPU address
; range #4000-#7FFF (#C4..#C7); #C0 restores the plain 64K.  Two facts
; make this safe mid-game:
;   * the video hardware ALWAYS reads the main 64K, so the screen
;     buffer at #4000 keeps displaying even while the CPU sees bank
;     RAM at those addresses;
;   * only code/stack/data OUTSIDE #4000-#7FFF may be touched while a
;     bank is in -- our code and stack live below #4000 by design.
; We keep interrupts off during a switch anyway: cheap insurance.
;
; All level blobs are copied into bank 4 at boot; entering a level
; copies one blob back down into level_buffer (main RAM).  That copy
; is the live, WRITABLE level: keycards vanish and doors open by
; editing it, and a respawn just fetches a fresh copy from the bank.
;
; ======================================================================
copy_levels_to_bank:
        di
        ld bc,#7FC4             ; extra page 0 (bank 4) over #4000
        out (c),c
        ld hl,levels_blob
        ld de,#4000
        ld bc,LEVELS_BLOB_LEN
        ldir
        ld bc,#7FC0             ; straight 64K again
        out (c),c
        ei
        ret

; ----------------------------------------------------------------------
; load_level -- fetch level A (1..LEVEL_COUNT) from bank 4 into
; level_buffer, wire the current_* pointers, spawn its drones.
; ----------------------------------------------------------------------
load_level:
        dec a                   ; table entry: word offset, word length
        add a,a
        add a,a
        ld e,a
        ld d,0
        ld hl,level_table
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld c,(hl)
        inc hl
        ld b,(hl)               ; BC = length, DE = offset in the bank
        ld hl,#4000
        add hl,de               ; HL = source (inside the banked page)
        ld de,level_buffer
        di
        push bc
        ld bc,#7FC4
        out (c),c               ; bank in...
        pop bc
        ldir                    ; ...copy the level down to main RAM...
        ld bc,#7FC0
        out (c),c               ; ...bank out
        ei
        ; --- wire the pointers into the fresh copy
        ld hl,level_buffer+4    ; map follows the 2-word header
        ld (current_map),hl
        ld de,level_buffer
        ld hl,(level_buffer)    ; header +0: rects offset
        add hl,de
        ld (current_rects),hl
        ld hl,(level_buffer+2)  ; header +2: drone list offset
        add hl,de
        ; --- unpack drone spawns into the runtime array
        ld a,(hl)               ; A = spawn count
        inc hl
        ld ix,drones
        ld b,MAX_DRONES
ll_slot:
        push bc
        or a
        jr z,ll_empty           ; out of spawns: clear remaining slots
        dec a
        ld (ix+0),1             ; active
        ld c,(hl)
        inc hl
        ld (ix+1),c             ; x
        ld c,(hl)
        inc hl
        ld (ix+2),c             ; y
        ld c,(hl)
        inc hl
        ld (ix+3),c             ; dir: start moving right (+speed)
        ld (ix+4),c             ; speed
        ld c,(hl)
        inc hl
        ld (ix+5),c             ; patrol left bound
        ld c,(hl)
        inc hl
        ld (ix+6),c             ; patrol right bound
        ld (ix+7),0             ; animation ticker
        jr ll_next
ll_empty:
        ld (ix+0),0
ll_next:
        pop bc
        ld de,8
        add ix,de
        djnz ll_slot
        ret

; ----------------------------------------------------------------------
; enter_level -- place the player at the bottom, reset the sprite
; bookkeeping, paint the room, drop into the game loop.  Entered by
; JP; resets SP so no path can leak stack.
; ----------------------------------------------------------------------
enter_level:
        ld sp,#1000
        ld a,(player_x)
        ld (respawn_x),a        ; where death brings us back
        ld a,176                ; emerge from the floor of the new room
        ld (player_y),a
        xor a
        ld (player_yfrac),a
        ld (last_fall),a
        ld hl,0
        ld (player_vy),hl
        ; arriving on the entry ladder? keep climbing; else stand
        ld a,(player_x)
        ld b,a
        ld c,176
        call find_ladder
        jr nc,el_ground
        ld (player_x),a         ; snap to the rails
        ld a,ST_CLIMB
        jr el_state
el_ground:
        ld a,ST_GROUND
el_state:
        ld (player_state),a
        ; both buffers' "previous image" slots = the spawn point
        ld a,(player_x)
        ld (prev_pos),a
        ld (prev_pos+2),a
        ld a,(player_y)
        ld (prev_pos+1),a
        ld (prev_pos+3),a
        ld ix,drones            ; same for every drone slot
        ld hl,drone_prev
        ld b,MAX_DRONES
el_dslot:
        ld a,(ix+1)
        ld (hl),a
        inc hl
        ld a,(ix+2)
        ld (hl),a
        inc hl
        ld a,(ix+1)
        ld (hl),a
        inc hl
        ld a,(ix+2)
        ld (hl),a
        inc hl
        ld de,8
        add ix,de
        djnz el_dslot
        ; paint the room into BOTH buffers (the visible top-to-bottom
        ; sweep is our flip-screen transition effect)
        ld a,SCREEN_B/256
        ld (draw_page),a
        call draw_tilemap
        ld a,SCREEN_A/256
        ld (draw_page),a
        call draw_tilemap
        ld a,(shown_r12)        ; then aim drawing at the hidden buffer
        cp CRTC_R12_A
        ld a,SCREEN_B/256
        jr z,el_aim
        ld a,SCREEN_A/256
el_aim:
        ld (draw_page),a
        jp game_loop

; ----------------------------------------------------------------------
; next_level -- the player climbed off the top edge (y < 8)
; ----------------------------------------------------------------------
next_level:
        ld a,(current_level)
        inc a
        cp LEVEL_COUNT+1
        jp nc,game_win          ; above the last level: the airlock
        ld (current_level),a
        call load_level
        jp enter_level          ; X carries over; Y resets to the floor

game_win:                       ; reached the top of the shaft (for now:
        ld b,50                 ; a green flash, then back to the title)
gw_loop:
        push bc
        call frame_sync
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#52                ; bright green border
        out (c),a
        pop bc
        djnz gw_loop
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#54
        out (c),a
        jp menu_screen

; ----------------------------------------------------------------------
; player_die -- drone contact.  Crash noise, red flash, respawn from a
; fresh copy of the level -- or back to the menu when the lives run out.
; ----------------------------------------------------------------------
player_die:
        ld a,SFX_HIT
        call sfx_start
        ld b,35                 ; freeze ~0.7s
pd_loop:
        push bc
        call frame_sync
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#4C                ; bright red border
        out (c),a
        call sfx_update         ; let the noise burst play out
        pop bc
        djnz pd_loop
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#54                ; border back to black
        out (c),a
        ld hl,game_lives
        dec (hl)
        jp z,menu_screen        ; out of lives
        ld a,(current_level)
        call load_level         ; fresh map (doors shut, cards back)
        ld a,(respawn_x)
        ld (player_x),a
        jp enter_level

; ======================================================================
;
;   SECURITY DRONES -- Prompt 5
;
; Each drone is an 8-byte record (see the constants block): position,
; a signed direction that doubles as the speed, patrol bounds and an
; animation ticker.  They bounce between xmin and xmax forever, and
; their rotor blades alternate between two sprite frames every 8
; frames (bit 3 of the ticker).
;
; ======================================================================
update_drones:
        ld ix,drones
        ld b,MAX_DRONES
ud_loop:
        ld a,(ix+0)
        or a
        jr z,ud_next
        inc (ix+7)              ; animation ticker (bit 3 picks the frame)
        ld a,(ix+1)
        add a,(ix+3)            ; x += dir  (dir is +/-speed)
        ld (ix+1),a
        cp (ix+5)               ; reached the left end of the patrol?
        jr c,ud_turn_r
        jr z,ud_turn_r
        cp (ix+6)               ; the right end?
        jr nc,ud_turn_l
        jr ud_next
ud_turn_r:
        ld a,(ix+5)
        ld (ix+1),a             ; clamp to the bound...
        ld a,(ix+4)
        ld (ix+3),a             ; ...and head right (+speed)
        jr ud_next
ud_turn_l:
        ld a,(ix+6)
        ld (ix+1),a
        xor a
        sub (ix+4)
        ld (ix+3),a             ; head left (-speed)
ud_next:
        ld de,8
        add ix,de
        djnz ud_loop
        ret

; ----------------------------------------------------------------------
; check_drone_hit -- player box vs every active drone box.
; Both boxes are 4 bytes x 16 lines, so AABB overlap reduces to
; |dx| < 4 AND |dy| < 16.  Carry set = contact (the caller jumps to
; player_die -- keeping the control flow in the game loop).
; ----------------------------------------------------------------------
check_drone_hit:
        ld ix,drones
        ld b,MAX_DRONES
cdh_loop:
        ld a,(ix+0)
        or a
        jr z,cdh_next
        ld a,(player_x)
        sub (ix+1)
        jr nc,cdh_dx
        neg                     ; |player_x - drone_x|
cdh_dx:
        cp SPR_W_BYTES
        jr nc,cdh_next          ; too far apart horizontally
        ld a,(player_y)
        sub (ix+2)
        jr nc,cdh_dy
        neg
cdh_dy:
        cp SPR_H_LINES
        jr nc,cdh_next
        scf                     ; boxes intersect: contact
        ret
cdh_next:
        ld de,8
        add ix,de
        djnz cdh_loop
        or a                    ; carry clear: safe
        ret

; ----------------------------------------------------------------------
; render_entities -- the per-frame draw pass, double-buffer aware:
; restore the background under every OLD image in this buffer, then
; draw everything anew (drones first, player on top).
; ----------------------------------------------------------------------
render_entities:
        call erase_player
        ld ix,drones
        ld iy,drone_prev
        ld b,MAX_DRONES
re_erase:
        ld a,(ix+0)
        or a
        jr z,re_e_next
        push bc
        ld a,(buf_index)        ; this buffer's slot: +0/+2 per drone
        add a,a
        ld e,a
        ld d,0
        push iy
        pop hl
        add hl,de
        ld b,(hl)
        inc hl
        ld c,(hl)
        call restore_tiles      ; (leaves IX/IY alone)
        pop bc
re_e_next:
        ld de,8
        add ix,de
        ld de,4
        add iy,de
        djnz re_erase
        ld ix,drones
        ld iy,drone_prev
        ld b,MAX_DRONES
re_draw:
        ld a,(ix+0)
        or a
        jr z,re_d_next
        push bc
        ld a,(buf_index)
        add a,a
        ld e,a
        ld d,0
        push iy
        pop hl
        add hl,de
        ld a,(ix+1)
        ld (hl),a               ; remember where we draw (for the
        ld b,a                  ;  restore two frames from now)
        inc hl
        ld a,(ix+2)
        ld (hl),a
        ld c,a
        ; the animation ticker's bit 3 swaps frames every 8 frames
        ld de,spr_drone_a
        ld a,(ix+7)
        and 8
        jr z,re_frame
        ld de,spr_drone_b
re_frame:
        call draw_sprite_8x16
        pop bc
re_d_next:
        ld de,8
        add ix,de
        ld de,4
        add iy,de
        djnz re_draw
        jp draw_player          ; player last: always in front

; ======================================================================
;
;   KEYCARDS AND SECURITY DOORS -- Prompt 7
;
; Keycards are TILES: touch one and it is edited out of the map RAM,
; re-blitted away on both screens, and counted.  Doors are RECTS (so
; the ordinary collision keeps blocking) plus tiles (so they show):
; pushing into one with a card deletes the rect and clears the tiles.
;
; ======================================================================
check_keycards:
        ld a,(player_x)         ; scan the same <=2x3 cell neighbourhood
        srl a                   ; the sprite can overlap
        srl a
        ld d,a
        ld a,(player_y)
        srl a
        srl a
        srl a
        ld e,a
        ld b,2
ck_col:
        ld c,3
ck_row:
        push bc
        call ck_cell
        pop bc
        inc e
        dec c
        jr nz,ck_row
        dec e                   ; row back to the top of the window
        dec e
        dec e
        inc d
        djnz ck_col
        ret
ck_cell:                        ; if cell (D,E) is a keycard, take it
        ld a,d
        cp MAP_W
        ret nc
        ld a,e
        cp MAP_H
        ret nc
        call map_cell_addr      ; (preserves D,E)
        ld a,(hl)
        cp TILE_KEYCARD
        ret nz
        ld (hl),TILE_EMPTY      ; lift it out of the map RAM...
        call redraw_cell_both   ; ...and off both screen buffers
        ld hl,keycards_held
        inc (hl)
        ld a,SFX_PING
        jp sfx_start            ; (preserves D,E for the loop)

; ----------------------------------------------------------------------
; check_doors -- pushing sideways into a DOOR rect while holding a
; card?  Then consume the card, kill the rect (type 0 matches nothing
; ever again) and clear the door's tiles from map + both screens.
; The walk itself goes through next frame -- the door is simply gone.
; ----------------------------------------------------------------------
check_doors:
        ld a,(keycards_held)
        or a
        ret z                   ; no card: the door stays shut
        ld a,(input_held)
        and INP_HORIZ_MASK
        ret z                   ; not pushing sideways
        ld a,(input_held)
        bit INP_LEFT,a
        ld a,(player_x)
        jr z,cdo_right
        or a
        ret z
        dec a                   ; probe one byte into the push
        jr cdo_probe
cdo_right:
        cp SCR_W_BYTES-SPR_W_BYTES
        ret nc
        inc a
cdo_probe:
        ld b,a
        ld a,(player_y)
        ld c,a
        ld d,SPR_W_BYTES
        ld e,SPR_H_LINES
        push bc
        call probe_level
        pop bc
        and TYPE_DOOR
        ret z                   ; nothing door-like in the way
        ld ix,(current_rects)   ; find WHICH door the box touches
cdo_find:
        ld a,(ix+0)
        cp #FF
        ret z
        ld a,(ix+4)
        and TYPE_DOOR
        jr z,cdo_skip
        call box_overlap        ; B,C,D,E probe box is still intact
        jr c,cdo_open
cdo_skip:
        repeat 5
        inc ix
        rend
        jr cdo_find
cdo_open:
        ld hl,keycards_held
        dec (hl)
        ld (ix+4),0             ; the rect will never match again
        ld a,SFX_PING
        call sfx_start
        ld a,(ix+0)             ; the door is 1 column x H rows of
        srl a                   ; tile-aligned cells: clear them all
        srl a
        ld d,a
        ld a,(ix+2)
        srl a
        srl a
        srl a
        ld e,a
        ld a,(ix+3)
        srl a
        srl a
        srl a
        ld b,a                  ; B = rows of door tile
cdo_clear:
        push bc
        call map_cell_addr
        ld (hl),TILE_EMPTY
        call redraw_cell_both
        pop bc
        inc e
        djnz cdo_clear
        ret

; ======================================================================
;
;   TEXT, FONT AND HUD -- Prompt 7 (and the menu)
;
; The font is 38 8x8 glyphs stored as 1-bit bitmaps (bit 7 = leftmost
; pixel), expanded to Mode 0 at draw time through pen_left, so any
; text can be any pen.  Glyphs 0..15 are the hex digits, which makes
; the HUD a single draw_char of the raw value.
;
; ======================================================================
; draw_char -- A=glyph, B=x byte, C=y line, E=pen.
; y MUST sit on a character row (multiple of 8): every glyph line is
; then a constant +#800 apart, like the tiles.
draw_char:
        push bc
        ld l,a                  ; IX = font + glyph*8
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        ld bc,font_8x8
        add hl,bc
        push hl
        pop ix
        ld d,0                  ; D/E = this pen's left/right pixel bits
        ld hl,pen_left
        add hl,de
        ld a,(hl)
        ld d,a
        srl a
        ld e,a
        pop bc
        push de
        call screen_addr        ; HL = destination (eats DE)
        pop de
        ld b,8
dchr_row:
        push hl
        ld c,(ix+0)             ; glyph row, bit 7 = leftmost pixel
        inc ix
        repeat 4                ; 8 pixels -> 4 screen bytes
        xor a
        rlc c                   ; left pixel -> carry
        jr nc,$+3               ; (hop over the 1-byte OR)
        or d
        rlc c                   ; right pixel -> carry
        jr nc,$+3
        or e
        ld (hl),a
        inc hl
        rend
        pop hl
        ld a,h                  ; +#800: rows stay inside the char row
        add a,8
        ld h,a
        djnz dchr_row
        ret

; draw_char_2x -- as draw_char but doubled: 16x16 pixels, any y.
; Each glyph pixel becomes a full byte (both Mode 0 pixels) and each
; glyph row paints two scanlines, stepping with the wrap-safe walk.
draw_char_2x:
        push bc
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        ld bc,font_8x8
        add hl,bc
        push hl
        pop ix
        ld d,0
        ld hl,pen_left
        add hl,de
        ld a,(hl)
        ld d,a
        srl a
        ld e,a
        pop bc
        push de
        call screen_addr
        pop de
        ld b,8                  ; 8 glyph rows...
d2x_row:
        push bc
        ld b,2                  ; ...two scanlines each
d2x_half:
        push hl
        ld c,(ix+0)
        repeat 8                ; every glyph pixel -> one full byte
        xor a
        rlc c
        jr nc,$+4               ; (hop over OR d + OR e)
        or d
        or e
        ld (hl),a
        inc hl
        rend
        pop hl
        ld a,h                  ; generic step: 16 lines cross a row
        add a,8
        ld h,a
        and #38
        jr nz,d2x_ok
        ld a,l
        add a,#50
        ld l,a
        ld a,h
        adc a,#C0
        ld h,a
d2x_ok:
        djnz d2x_half
        inc ix
        pop bc
        djnz d2x_row
        ret

; ----------------------------------------------------------------------
; draw_text / draw_text_2x -- HL = 0-terminated ASCII, B=x, C=y, E=pen
; ----------------------------------------------------------------------
draw_text:
        ld a,(hl)
        or a
        ret z
        inc hl
        push hl
        push bc
        push de
        call ascii_to_glyph
        call draw_char
        pop de
        pop bc
        pop hl
        ld a,b
        add a,4                 ; one char = 4 Mode 0 bytes
        ld b,a
        jr draw_text
draw_text_2x:
        ld a,(hl)
        or a
        ret z
        inc hl
        push hl
        push bc
        push de
        call ascii_to_glyph
        call draw_char_2x
        pop de
        pop bc
        pop hl
        ld a,b
        add a,8
        ld b,a
        jr draw_text_2x

ascii_to_glyph:                 ; ASCII -> font index (0-9 A-Z '-' ' ')
        cp ' '
        jr nz,atg1
        ld a,37
        ret
atg1:
        cp '-'
        jr nz,atg2
        ld a,36
        ret
atg2:
        cp 'A'
        jr c,atg3
        sub 'A'-10              ; letters follow the ten digits
        ret
atg3:
        sub '0'
        ret

; ----------------------------------------------------------------------
; draw_hud -- keycards held (left, green) and lives (right, red).
; Drawn into the hidden buffer every frame: two glyphs, trivial cost,
; and both buffers stay correct without any dirty-tracking.
; ----------------------------------------------------------------------
draw_hud:
        ld a,(keycards_held)
        and #0F                 ; one hex digit (glyph = value)
        ld b,0
        ld c,0
        ld e,9                  ; keycard green
        call draw_char
        ld a,(game_lives)
        and #0F
        ld b,SCR_W_BYTES-4
        ld c,0
        ld e,5                  ; alarm red
        jp draw_char

; ======================================================================
;
;   TITLE MENU
;
; ======================================================================
menu_screen:
        ld sp,#1000             ; fresh stack (see game_loop note)
        xor a
        ld (sfx_timer),a
        call sfx_silence
        ld hl,menu_map          ; backdrop straight from main RAM
        ld (current_map),hl
        ld a,SCREEN_B/256
        ld (draw_page),a
        call draw_menu_page
        ld a,SCREEN_A/256
        ld (draw_page),a
        call draw_menu_page
ms_loop:
        call frame_sync
        call flip_buffers
        call read_input
        ld hl,frame_ctr
        inc (hl)
        ld a,(frame_ctr)        ; blink the prompt every 16 frames
        and 16
        ld e,6                  ; orange on...
        jr nz,ms_blink
        ld e,0                  ; ...black off (opaque glyphs erase)
ms_blink:
        ld hl,txt_press
        ld b,2
        ld c,120
        call draw_text
        ld a,(input_new)
        bit INP_FIRE,a
        jr z,ms_loop
        ; --- new game
        ld a,START_LIVES
        ld (game_lives),a
        xor a
        ld (keycards_held),a
        ld a,1
        ld (current_level),a
        ld a,38                 ; the mechanic's post, mid-deck
        ld (player_x),a
        ld a,1
        call load_level
        jp enter_level

draw_menu_page:                 ; backdrop + text + diorama, one buffer
        call draw_tilemap
        ld hl,txt_title
        ld b,4
        ld c,32
        ld e,7                  ; bright yellow, double size
        call draw_text_2x
        ld hl,txt_tag
        ld b,4
        ld c,64
        ld e,10                 ; bright cyan
        call draw_text
        ld hl,txt_credit
        ld b,6                  ; bottom middle
        ld c,184
        ld e,2                  ; steel grey
        call draw_text
        ld b,36                 ; the mechanic, on the crate stack
        ld c,128
        ld de,spr_mechanic
        call draw_sprite_8x16
        ld b,56                 ; a drone hovering watchfully
        ld c,88
        ld de,spr_drone_a
        jp draw_sprite_8x16

; ======================================================================
;
;   SOUND EFFECTS -- Prompt 8: driving the AY-3-8912
;
; The AY sits behind the PPI: its data bus is PPI port A (#F4xx) and
; its BDIR/BC1 control lines are PPI port C bits 7/6 (#F6xx).  One
; register write is a two-step dance: put the register number on port
; A and pulse BDIR+BC1 (%11 = "latch address"), then put the value on
; port A and pulse BDIR alone (%10 = "write data").
;
; Registers used here:
;   R0/R1  channel A tone period (12 bits; 1MHz/16/period = Hz --
;          SMALLER period = HIGHER pitch)
;   R6     noise period (5 bits, larger = deeper rumble)
;   R7     mixer: bits 0-2 enable tone A/B/C, bits 3-5 noise per
;          channel -- all ACTIVE LOW.  Bit 6 is the I/O port
;          direction and MUST stay 0 or the keyboard stops working!
;   R8     channel A volume (0-15)
;
; One sound at a time on channel A; a new trigger simply replaces the
; old.  sfx_update writes a fresh register burst every frame:
;   JUMP : square tone whose period shrinks each frame - rising chirp
;   HIT  : pure noise burst, volume ramping 12->0 - a metallic crunch
;   PING : short high tone, quick fade - the keycard chime
; ======================================================================
psg_write:                      ; A = AY register, E = value
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

sfx_start:                      ; A = SFX_* (1-based); sets the timer
        ld (sfx_type),a         ; (preserves DE -- callers rely on it)
        ld hl,sfx_len_tab-1
        add a,l
        ld l,a
        jr nc,$+3
        inc h
        ld a,(hl)
        ld (sfx_timer),a
        ret

sfx_update:                     ; call once per frame
        ld a,(sfx_timer)
        or a
        ret z                   ; silent, and already shut down
        dec a
        ld (sfx_timer),a
        jr z,sfx_silence        ; just expired: close the channel
        ld a,(sfx_type)
        dec a
        jr z,sfx_upd_jump
        dec a
        jr z,sfx_upd_hit
        ; ---- PING: high steady tone (period 40 ~= 1.5kHz), fast fade
        xor a
        ld e,40
        call psg_write          ; R0 = period low
        ld a,1
        ld e,0
        call psg_write          ; R1 = period high
        ld a,7
        ld e,%00111110          ; mixer: tone A only (bit 6 low!)
        call psg_write
        ld a,(sfx_timer)
        add a,6                 ; volume 13..7 as the timer runs out
        ld e,a
        ld a,8
        jp psg_write
sfx_upd_jump:
        ; ---- JUMP: period 60+timer*16 -- shrinks every frame, so the
        ; pitch RISES as we leave the ground (timer 12 -> 252 .. 76)
        ld a,(sfx_timer)
        add a,a
        add a,a
        add a,a
        add a,a
        add a,60
        ld e,a
        xor a
        call psg_write          ; R0
        ld a,1
        ld e,0
        call psg_write          ; R1
        ld a,7
        ld e,%00111110          ; tone A only
        call psg_write
        ld a,8
        ld e,12                 ; steady volume; the sweep does the work
        jp psg_write
sfx_upd_hit:
        ; ---- HIT: white noise, volume halving with the timer (12->0)
        ld a,6
        ld e,14                 ; deep noise period: metallic rumble
        call psg_write
        ld a,7
        ld e,%00110111          ; noise on channel A, tones all off
        call psg_write
        ld a,(sfx_timer)
        srl a
        ld e,a
        ld a,8
        jp psg_write
sfx_silence:
        ld a,8
        ld e,0
        call psg_write          ; volume hard off
        ld a,7
        ld e,%00111111          ; mixer: everything off (bit 6 = 0)
        jp psg_write

sfx_len_tab:
        defb 12,24,8            ; frames: JUMP, HIT, PING

; ======================================================================
; set_palette -- program all 16 inks + border via the Gate Array
;
; GA commands on port #7Fxx: %00nnnnnn selects pen n (or #10 = border),
; %01cccccc sets that pen to hardware colour c.  The values in
; palette_data below already include the %01 command bits.
; ======================================================================
set_palette:
        ld hl,palette_data
        ld b,GA_PORT/256        ; B = #7F
        xor a                   ; pen 0
sp_pen:
        out (c),a               ; select pen
        ld e,(hl)
        out (c),e               ; set its colour
        inc hl
        inc a
        cp 16
        jr c,sp_pen
        ld a,#10                ; select the border...
        out (c),a
        ld e,#54                ; ...and paint it black
        out (c),e
        ret

; ----------------------------------------------------------------------
; clear_buffers -- fill both 16K screen buffers with pen 0
; ----------------------------------------------------------------------
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
        ldir                    ; smear the first zero across 16K
        ret

; ----------------------------------------------------------------------
; Interrupt stub -- copied to #0038 at init (IM 1 vector)
;
; The Gate Array interrupts 6 times per frame (300 Hz).  All we do is
; count ticks; that makes HALT usable as a scheduler and gives future
; code (music player, steam-vent timers) a free 300 Hz heartbeat.
; Position-independent, so it can be LDIR'd to #0038 as-is.
; ----------------------------------------------------------------------
int_stub:
        push af
        push hl
        ld hl,frame_ticks
        inc (hl)
        pop hl
        pop af
        ei
        ret
int_stub_end:

; ======================================================================
; DATA
; ======================================================================

; ----------------------------------------------------------------------
; Palette -- 16 Gate Array hardware colours (%01 command bits included)
; A grimy industrial set for the bottom of the shaft.
; ----------------------------------------------------------------------
palette_data:
        defb #54    ; pen 0 : black          - background, darkness
        defb #4B    ; pen 1 : bright white   - highlights, text
        defb #40    ; pen 2 : grey/white     - steel platforms, boots
        defb #44    ; pen 3 : blue           - worker overalls, shadow steel
        defb #57    ; pen 4 : sky blue       - cold industrial light
        defb #4C    ; pen 5 : bright red     - hazard lights, alarms
        defb #4E    ; pen 6 : orange         - rust, warning stripes
        defb #4A    ; pen 7 : bright yellow  - lamps, hard hats
        defb #47    ; pen 8 : pink           - skin
        defb #52    ; pen 9 : bright green   - terminals, keycard A
        defb #53    ; pen 10: bright cyan    - steam, glass
        defb #45    ; pen 11: purple         - elite insignia (top levels)
        defb #5C    ; pen 12: red            - dried warning paint
        defb #56    ; pen 13: green          - corroded copper
        defb #5E    ; pen 14: yellow         - sodium-light falloff
        defb #5F    ; pen 15: pastel blue    - distant metalwork

; ----------------------------------------------------------------------
; Player sprite -- "the mechanic", 8x16 px, masked
; Generated by tools/sprite_gen.py (edit the ASCII art there, re-run,
; paste).  Pens: 7=hard hat  8=skin  3=overalls  2=boots
; ----------------------------------------------------------------------
spr_mechanic:   ; 8x16 px = 4 bytes x 16 lines, (mask,data) interleaved
        defb #FF,#00, #00,#FC, #00,#FC, #FF,#00   ; ..7777..
        defb #AA,#54, #00,#FC, #00,#FC, #55,#A8   ; .777777.
        defb #AA,#54, #00,#03, #00,#03, #55,#A8   ; .788887.
        defb #AA,#54, #00,#03, #00,#03, #55,#A8   ; .788887.
        defb #FF,#00, #00,#03, #00,#03, #FF,#00   ; ..8888..
        defb #FF,#00, #00,#CC, #00,#CC, #FF,#00   ; ..3333..
        defb #AA,#44, #00,#CC, #00,#CC, #55,#88   ; .333333.
        defb #00,#46, #00,#CC, #00,#CC, #00,#89   ; 83333338
        defb #00,#46, #00,#CC, #00,#CC, #00,#89   ; 83333338
        defb #AA,#44, #00,#CC, #00,#CC, #55,#88   ; .333333.
        defb #AA,#44, #00,#CC, #00,#CC, #55,#88   ; .333333.
        defb #AA,#44, #55,#88, #AA,#44, #55,#88   ; .33..33.
        defb #AA,#44, #55,#88, #AA,#44, #55,#88   ; .33..33.
        defb #AA,#44, #55,#88, #AA,#44, #55,#88   ; .33..33.
        defb #AA,#04, #55,#08, #AA,#04, #55,#08   ; .22..22.
        defb #AA,#04, #55,#08, #AA,#04, #55,#08   ; .22..22.

; ----------------------------------------------------------------------
; Security drone, two frames -- the rotor spins and the eye scans.
; Generated by tools/sprite_gen.py.  Pens: 2 rotor, 10 shell, 5 eye.
; ----------------------------------------------------------------------
spr_drone_a:
        defb #55,#08, #AA,#04, #55,#08, #AA,#04   ; 2..22..2
        defb #AA,#04, #00,#0C, #00,#0C, #55,#08   ; .222222.
        defb #FF,#00, #AA,#04, #55,#08, #FF,#00   ; ...22...
        defb #FF,#00, #00,#0F, #00,#0F, #FF,#00   ; ..aaaa..
        defb #AA,#05, #00,#0F, #00,#0F, #55,#0A   ; .aaaaaa.
        defb #AA,#05, #00,#F0, #00,#F0, #55,#0A   ; .a5555a.
        defb #AA,#05, #00,#0F, #00,#0F, #55,#0A   ; .aaaaaa.
        defb #AA,#05, #00,#0F, #00,#0F, #55,#0A   ; .aaaaaa.
        defb #FF,#00, #00,#0F, #00,#0F, #FF,#00   ; ..aaaa..
        defb #FF,#00, #AA,#05, #55,#0A, #FF,#00   ; ...aa...
        defb #FF,#00, #55,#08, #AA,#04, #FF,#00   ; ..2..2..
        defb #AA,#04, #FF,#00, #FF,#00, #55,#08   ; .2....2.
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
spr_drone_b:
        defb #AA,#04, #55,#08, #AA,#04, #55,#08   ; .22..22.
        defb #00,#0C, #00,#0C, #00,#0C, #00,#0C   ; 22222222
        defb #FF,#00, #AA,#04, #55,#08, #FF,#00   ; ...22...
        defb #FF,#00, #00,#0F, #00,#0F, #FF,#00   ; ..aaaa..
        defb #AA,#05, #00,#0F, #00,#0F, #55,#0A   ; .aaaaaa.
        defb #AA,#05, #00,#E0, #00,#D0, #55,#0A   ; .a5115a.
        defb #AA,#05, #00,#0F, #00,#0F, #55,#0A   ; .aaaaaa.
        defb #AA,#05, #00,#0F, #00,#0F, #55,#0A   ; .aaaaaa.
        defb #FF,#00, #00,#0F, #00,#0F, #FF,#00   ; ..aaaa..
        defb #FF,#00, #AA,#05, #55,#0A, #FF,#00   ; ...aa...
        defb #FF,#00, #55,#08, #AA,#04, #FF,#00   ; ..2..2..
        defb #AA,#04, #FF,#00, #FF,#00, #55,#08   ; .2....2.
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........
        defb #FF,#00, #FF,#00, #FF,#00, #FF,#00   ; ........

; ----------------------------------------------------------------------
; Menu / UI strings (ASCII; ascii_to_glyph maps them to the font)
; ----------------------------------------------------------------------
txt_title:      defb "THE SHAFT",0
txt_tag:        defb "THE TRUTH IS ABOVE",0
txt_press:      defb "PRESS FIRE TO START",0
txt_credit:     defb "REVIVE8BIT - 2026",0

; ----------------------------------------------------------------------
; Generated data -- tools/level_gen.py emits src/levels.asm:
; tileset, font, pen table, menu backdrop, all level blobs (tilemaps +
; collision rects + drone spawns compiled from the same ASCII source),
; and the bank offset table.
; ----------------------------------------------------------------------
        include "levels.asm"

; ----------------------------------------------------------------------
; line_offsets -- offset of each scanline's first byte within a 16K
; screen, generated at assembly time:
;     offset(y) = (y AND 7)*#800 + (y/8)*80
; Built with nested repeats (25 character rows of 8 scanlines) rather
; than division, because rasm evaluates "/" in floating point.
; ----------------------------------------------------------------------
line_offsets:
lrow=0
        repeat 25               ; 25 character rows down the screen...
lline=0
        repeat 8                ; ...of 8 interleaved scanlines each
        defw lline*#800+lrow*80
lline=lline+1
        rend
lrow=lrow+1
        rend

; ----------------------------------------------------------------------
; Variables
; ----------------------------------------------------------------------
frame_ticks:    defb 0          ; ++ at 300 Hz by the interrupt stub
input_held:     defb 0          ; current input flags (1 = held)
input_new:      defb 0          ; flags that turned on this frame
shown_r12:      defb 0          ; CRTC R12 value currently displayed
draw_page:      defb 0          ; high byte of the DRAW buffer (#40/#C0)
buf_index:      defb 0          ; 0/1 - selects the prev_pos slot below

; Current-screen pointers: menu_screen and load_level re-aim these
; when the displayed room changes.
current_map:     defw menu_map  ; 20x25 tile indices being displayed
current_tileset: defw tileset   ; pixel data the indices refer to
current_rects:   defw level_buffer ; collision geometry of this screen

player_x:       defb 38         ; byte column (0..76), start mid-deck
player_yfrac:   defb 0          ; fractional Y, 1/256 line.  MUST sit
player_y:       defb 176        ; directly before player_y: the pair is
                                ; one little-endian 8.8 word (L=frac,
                                ; H=line) for the physics integrator.
player_state:   defb ST_GROUND  ; ST_GROUND / ST_CLIMB / ST_AIR
player_vy:      defw 0          ; vertical velocity, signed 8.8
fall_start:     defb 176        ; highest y reached in the current arc
last_fall:      defb 0          ; lines dropped on the last landing
                                ; (hook for fall damage / landing thud)

frame_ctr:      defb 0          ; ++ every game/menu frame (blink, anim)
game_lives:     defb START_LIVES
keycards_held:  defb 0
current_level:  defb 1
respawn_x:      defb 38         ; where this level was entered
sfx_type:       defb 0          ; active sound effect (0 = none)
sfx_timer:      defb 0          ; frames left on it

drones:         defs MAX_DRONES*8,0   ; runtime entity records
drone_prev:     defs MAX_DRONES*4,0   ; per drone: (x,y) x 2 buffers

; The live copy of the current level, fetched from bank 4.  Map +
; rects + drone spawns; WRITABLE (keycards/doors edit it) and big
; enough for the largest level with room to grow.
level_buffer:   defs 768,0
prev_pos:       defb 38,176     ; where the player was drawn in buffer B
                defb 38,176     ; ... and in buffer A
key_matrix:     defs 10,#FF     ; raw matrix rows (active low, #FF = idle)

        ; keep everything below the #4000 screen buffer
        assert $ < SCREEN_B
