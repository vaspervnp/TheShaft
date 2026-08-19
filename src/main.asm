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
INP_UP          equ 2           ; climb
INP_DOWN        equ 3           ; climb down / duck / slide
INP_FIRE        equ 4           ; jump (Space / joystick fire 1)
INP_ACT         equ 5           ; whip (Z / joystick fire 2)

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
MAX_ENTITIES    equ 8           ; enemies, debris and drips share it
ENT_SIZE        equ 10
; entity record layout (10 bytes):
;  +0 type  +1 x  +2 y  +3 dir (signed +/-speed)  +4 speed/interval
;  +5 xmin  +6 xmax  +7 animation ticker  +8 timer  +9 spare
ET_NONE         equ 0
ET_RIOT         equ 1           ; riot gear: slow patrol, shield up
ET_COAT         equ 2           ; long coat + crowbar: fast patrol
ET_THROW        equ 3           ; drops debris from the platform above
ET_PROJ         equ 4           ; the falling debris itself
ET_DYING        equ 5           ; being erased from both buffers
ET_DRIP         equ 6           ; falling lubricant (colour in +9)
ET_STEAM        equ 7           ; a vent's blast: a standing column
ET_BULLET       equ 8           ; a rifle round at gun height
ET_DRONE        equ 9           ; kamikaze flyer, homing on the player
BULLET_LVL      equ 10          ; guards start shooting at this level
DRONE_LVL       equ 20          ; ...and the drone dispatch wakes here
RIOT_RELOAD     equ 140         ; frames between a guard's shots
START_LIVES     equ 3
MAX_VENTS       equ 4           ; steam vents per level
STEAM_TIME      equ 45          ; frames a blast stays up

; Energy: every hit costs one point behind a 3-second immunity
; flicker; at zero a life goes and the tank refills.  Medical crates
; hold 1-4 points -- spilling past 5 banks a whole life.
ENERGY_MAX      equ 5
IMMUNE_TIME     equ 150         ; 3 seconds at 50 frames/s
MAX_LEAKS       equ 4           ; dripping ceiling pipes per level

; The whip: reach in bytes, active/cooldown timing
WHIP_LEN       equ 8           ; 16 pixels of rope
WHIP_TIME      equ 10          ; timer start; rope visible while >= 5
SLIDE_TIME      equ 12          ; frames of slide burst at 2 bytes/frame

; Fall damage: platforms sit 48 lines apart and a jump-down adds at
; most ~20 lines of arc, so a measured drop beyond 72 lines means MORE
; than one floor -- that costs a life.  Dropping through a single
; platform gap (exactly 48) is always safe.
FALL_HURT       equ 72

; Sound effects (see the AY section for what each does)
SFX_JUMP        equ 1
SFX_HIT         equ 2
SFX_PING        equ 3
SFX_WHIP        equ 4
SFX_KILL        equ 5
SFX_STEP        equ 6
SFX_RUNG        equ 7

STEP_FRAMES     equ 5           ; a footfall every 5 frames of walking

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

        ld hl,zone_palettes     ; boot in the machine-deck colours
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

        ; The BASIC loader already parked all 59 levels in extra-RAM
        ; banks 4-6 before CALLing us, so: straight to the menu.
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
        call update_music       ; the shaft's dirge, channels B+C
        call update_player      ; walk/climb/jump/fall state machine
        call step_tick          ; his boots on the deck, every 5 frames
        ld a,(player_y)
        cp 8                    ; crossed the top edge of the screen?
        jp c,next_level         ; up one level of the shaft
        ; climbing at the very bottom with Down still held? then the
        ; ladder continues into the level below
        ld a,(player_state)
        cp ST_CLIMB
        jr nz,gl_no_desc
        ld a,(player_y)
        cp 176
        jr c,gl_no_desc
        ld a,(input_held)
        bit INP_DOWN,a
        jr z,gl_no_desc
        ld a,(current_level)
        dec a
        jp nz,prev_level        ; (level 1's deck is the true bottom)
gl_no_desc:
        ; fell clean through the deck hole?  the shaft goes on below
        ld a,(player_y)
        cp 186
        jr c,gl_no_fdesc
        ld a,(current_level)
        dec a
        jp nz,prev_level
gl_no_fdesc:
        ld a,(fall_hit)         ; landed from more than one floor up?
        or a
        call nz,take_fall_hit
        call check_elevator     ; standing at a lift door + Up/Down?
        call update_entities    ; patrols, throwers, falling debris
        ld a,(whip_timer)      ; the thong out at full stretch? resolve
        cp WHIP_TIME-4         ; the crack against the just-moved foes
        call z,whip_hits
        call update_leaks       ; ceiling pipes shed their drops
        call update_vents       ; steam nozzles on the 300 Hz clock
        call update_drones      ; the kamikaze dispatch, level 20 up
        call check_enemy_hit    ; player box vs every hostile box
        call c,take_hit         ; costs energy, not (immediately) life
        call check_keycards     ; touching a keycard tile? collect it
        call check_doors        ; pushing a door with a card? open it
        call render_entities    ; restore tiles, draw everyone + rope
        call draw_hud           ; keycards (left) and lives (right)
        call draw_lift_panel    ; the LED floor readout over the lift
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

        ; ---- FIRE (jump) : joystick fire 1, or Space (line 5, bit 7)
        ld a,(key_matrix+5)
        cpl
        rlca                    ; Space -> carry
        jr c,ri_fire
        bit 5,b                 ; fire 1
        jr z,ri_no_fire
ri_fire:
        set INP_FIRE,c
ri_no_fire:

        ; ---- ACT (whip) : joystick fire 2, or Z (line 8, bit 7)
        ld a,(key_matrix+8)
        cpl
        rlca                    ; Z -> carry
        jr c,ri_act
        bit 4,b                 ; fire 2
        jr z,ri_no_act
ri_act:
        set INP_ACT,c
ri_no_act:

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
        ld hl,whip_timer       ; the whip recoils whatever we're doing
        ld a,(hl)
        or a
        jr z,up_lt
        dec (hl)
up_lt:
        ld hl,immune_timer      ; the hit flicker fades likewise
        ld a,(hl)
        or a
        jr z,up_im
        dec (hl)
up_im:
        ld a,(input_held)       ; facing follows the pushed direction
        bit INP_LEFT,a          ; (even when the step itself is blocked)
        jr z,up_fr
        ld a,1
        ld (player_facing),a    ; 1 = left
up_fr:
        ld a,(input_held)
        bit INP_RIGHT,a
        jr z,up_fd
        xor a
        ld (player_facing),a    ; 0 = right
up_fd:
        xor a                   ; ducking and "did we move" are
        ld (player_duck),a      ; recomputed every frame
        ld (player_moved),a
        ld a,(player_state)
        or a
        jr z,upd_ground
        dec a                   ; ST_CLIMB?
        jp z,upd_climb
        jp upd_air

; --------------------------- GROUNDED ---------------------------------
upd_ground:
        ld a,(input_new)        ; Fire = jump, on the PRESS edge only
        bit INP_FIRE,a
        jr z,ug_no_jump
        call start_jump         ; airborne with full upward velocity...
        jp move_horizontal      ; ...and may steer this same frame
ug_no_jump:
        ld a,(input_new)        ; Act = crack the whip (press edge,
        bit INP_ACT,a           ; only once the rope has recoiled)
        jr z,ug_no_whip
        ld a,(whip_timer)
        or a
        jr nz,ug_no_whip
        ld a,WHIP_TIME
        ld (whip_timer),a      ; game_loop resolves the hits this frame
        ; aim from the held cursor: Up = skyward, Up+side = diagonal,
        ; neither = the classic side whip
        ld a,(input_held)
        bit INP_UP,a
        ld a,0
        jr z,ug_aim
        ld a,(input_held)
        and INP_HORIZ_MASK
        ld a,1
        jr z,ug_aim
        ld a,2
ug_aim:
        ld (whip_dir),a
        ld a,SFX_WHIP
        call sfx_start
        ret                     ; planting the throw costs the frame
ug_no_whip:
        ld a,(input_held)       ; Up: try to mount a ladder
        bit INP_UP,a
        jr z,ug_no_up
        ld a,(player_y)
        sub 2
        ld c,a
        call try_mount
        ret c                   ; mounted: climbing consumed the frame
ug_no_up:
        ld a,(input_held)       ; Down: ladder first...
        bit INP_DOWN,a
        jr z,ug_down_off
        ld a,(player_y)
        add a,2
        ld c,a
        call try_mount
        ret c
        ; ...no ladder below: Down+direction = slide, Down alone = duck
        ld a,(input_held)
        and INP_HORIZ_MASK
        jr z,ug_duck
        ld a,(slide_lock)
        or a
        jr nz,ug_duck           ; one slide per Down press
        ld a,SLIDE_TIME
        ld (slide_timer),a
        ld a,1
        ld (slide_lock),a
        jr ug_duck
ug_down_off:
        xor a
        ld (slide_lock),a       ; Down released: the slide is re-armed
        jr ug_move
ug_duck:
        ld a,1                  ; low profile: throwers' debris and
        ld (player_duck),a      ; crowbar swings pass over your head
ug_move:
        ld a,(player_x)         ; a duct overhead?  then you STAY low:
        ld b,a                  ; standing up inside it is not on offer
        ld a,(player_y)
        ld c,a
        ld d,SPR_W_BYTES
        ld e,8                  ; just the head strip
        call probe_level
        rra
        jr nc,ug_no_roof
        ld a,2                  ; 2 = forced crouch: crawling allowed
        ld (player_duck),a
ug_no_roof:
        ld a,(slide_timer)      ; sliding: 2 bytes/frame burst in the
        or a                    ; facing direction, hitbox stays low
        jr z,ug_walk
        dec a
        ld (slide_timer),a
        ld a,1
        ld (player_duck),a
        call slide_step
        call slide_step
        jr ug_support
ug_walk:
        ld a,(player_duck)      ; ducking in place: no creeping -- but
        cp 1                    ; a FORCED crouch (duct overhead) may
        jr z,ug_support         ; crawl on through
        call move_horizontal
ug_support:
        call is_supported       ; is the ground still there?
        or a
        ret nz
        jp start_fall           ; slid or walked off an edge

; slide_step -- one try-then-commit byte in the facing direction
slide_step:
        ld a,(player_facing)
        or a
        jr nz,ss_left
        ld a,(player_x)
        cp SCR_W_BYTES-SPR_W_BYTES
        ret nc
        inc a
        jr ss_try
ss_left:
        ld a,(player_x)
        or a
        ret z
        dec a
ss_try:
        call solid_at_x
        ret c                   ; wall/crate/door stops the slide
        ld (player_x),a
        ld a,1
        ld (player_moved),a
        ret

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
        ; fall-height accounting: measured from the arc's apex
        ld a,(fall_start)
        ld b,a
        ld a,(player_y)
        sub b                   ; lines dropped since the arc's apex
        jr nc,ua_keep_fall
        xor a                   ; landed above the apex? then no fall
ua_keep_fall:
        ld (last_fall),a
        cp FALL_HURT+1          ; more than one floor?  that hurts --
        ret c                   ; the game loop collects the bruise
        ld a,1
        ld (fall_hit),a
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
        ld a,e
        ld (player_moved),a     ; feeds the walk animation
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
        ld a,e
        ld (player_moved),a
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
        ld e,SPR_H_LINES
        ld a,(player_duck)      ; crouched: only the low half collides,
        or a                    ; so a head-height duct lets you under
        jr z,sax_tall
        ld a,c
        add a,8
        ld c,a
        ld e,8
sax_tall:
        ld d,SPR_W_BYTES
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
        ld a,(immune_timer)     ; hit flicker: skip every other image
        or a
        jr z,dp_solid           ; (the slot above still gets restored)
        ld a,(frame_ctr)
        and 2
        ret nz
dp_solid:
        ld a,(player_duck)      ; crouched frame while ducking/sliding
        or a
        jr z,dp_stand
        ld de,spr_mech_duck
        jp draw_sprite_8x16
dp_stand:
        ; climbing has its own back view: he looks at the ladder, and
        ; his hands swap high/low as he goes (every 4 lines of height)
        ld a,(player_state)
        cp ST_CLIMB
        jr nz,dp_facing
        ld de,spr_mech_climb
        ld a,(player_y)
        and 4
        jr z,dp_draw
        jr dp_stride
dp_facing:
        ld de,spr_mech_r        ; face the way we last pushed
        ld a,(player_facing)
        or a
        jr z,dp_frame
        ld de,spr_mech_l
dp_frame:
        ; pick frame A (stand) or B (stride, +128 bytes):
        ;   airborne -- stride pose
        ;   walking  -- alternate every 8 frames, only while moving
        ld a,(player_state)
        cp ST_AIR
        jr z,dp_stride
dp_ground:
        ld a,(player_moved)
        or a
        jr z,dp_draw
        ld a,(frame_ctr)
        and 8
        jr z,dp_draw
dp_stride:
        ld hl,4                 ; frame B = the next 4-byte stub
        add hl,de
        ex de,hl
dp_draw:
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
        ; DE now names a COMPILED routine (src/sprites_c.asm, #8000+):
        ; the frame draws itself at HL.  PUSH+RET is the classic
        ; "jump to DE": the routine's own RET returns to our caller.
        push de
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
        ld d,SPR_W_BYTES        ; the classic sprite-sized case
        ld e,SPR_H_LINES
        ; fall through into the general version

; ----------------------------------------------------------------------
; restore_area -- re-blit every map cell covering an arbitrary
; rectangle: B=x (bytes), C=y (lines), D=w (bytes), E=h (lines).
;
; THE hot path of the frame (every sprite pays it), so no per-cell
; address maths: the map pointer and the screen pointer are computed
; ONCE for the top-left cell and then STEPPED -- map +20 a row and +1
; a column, screen +80 a row (character rows are linear at line 0)
; and +4 a column.  draw_tile does the pixel work.
; ----------------------------------------------------------------------
restore_area:
        ld a,b
        srl a
        srl a
        ld (ra_c0),a            ; first tile column
        ld a,b
        add a,d
        dec a
        srl a
        srl a
        cp MAP_W                ; clip the window to the map
        jr c,ra_cw
        ld a,MAP_W-1
ra_cw:
        ld (ra_c1),a
        ld a,c
        srl a
        srl a
        srl a
        ld (ra_r0),a
        ld a,c
        add a,e
        dec a
        srl a
        srl a
        srl a
        cp MAP_H
        jr c,ra_rw
        ld a,MAP_H-1
ra_rw:
        ld (ra_r1),a
        ; --- top-left cell: map pointer and screen pointer, once
        ld a,(ra_c0)
        ld d,a
        ld a,(ra_r0)
        ld e,a
        call map_cell_addr      ; HL -> map[r0][c0]  (trashes BC)
        ld (ra_map),hl
        ld a,(ra_c0)
        add a,a
        add a,a
        ld b,a                  ; x byte = col*4
        ld a,(ra_r0)
        add a,a
        add a,a
        add a,a
        ld c,a                  ; line = row*8
        call screen_addr        ; HL = screen top-left (line 0 of row)
        ld (ra_scr),hl
        ld a,(ra_c0)
        ld d,a                  ; D = walking column
ra_col:
        ld a,(ra_r0)
        ld e,a                  ; E = walking row
        ld hl,(ra_map)
        ld (ra_mp),hl
        ld hl,(ra_scr)
        ld (ra_sp),hl
ra_row:
        ld hl,(ra_mp)
        ld a,(hl)               ; the tile index under this cell
        push de
        ld de,(ra_sp)
        call draw_tile          ; (trashes A,BC,DE,HL)
        pop de
        ld hl,(ra_mp)           ; step one map row down...
        ld bc,MAP_W
        add hl,bc
        ld (ra_mp),hl
        ld hl,(ra_sp)           ; ...and one character row down
        ld bc,80
        add hl,bc
        ld (ra_sp),hl
        inc e
        ld a,(ra_r1)
        cp e
        jr nc,ra_row
        ld hl,(ra_map)          ; next column: map +1, screen +4
        inc hl
        ld (ra_map),hl
        ld hl,(ra_scr)
        ld bc,4
        add hl,bc
        ld (ra_scr),hl
        inc d
        ld a,(ra_c1)
        cp d
        jr nc,ra_col
        ret

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
; The 59 level blobs span THREE banks (4, 5 and 6): the BASIC loader
; pages each bank in and LOADs a levelsN.bin file straight into it
; before the game even starts.  Entering a level pages the right bank
; back in and copies one blob down into level_buffer (main RAM).
; That copy is the live, WRITABLE level: keycards vanish and doors
; open by editing it, and a respawn just fetches a fresh copy.
;
; ======================================================================
; load_level -- fetch level A (1..LEVEL_COUNT) from its bank into
; level_buffer, apply the zone palette, wire pointers, spawn enemies.
; Table entry (5 bytes): bank select value (#C4..#C6), word source
; address (#4000-based), word blob length.
; ----------------------------------------------------------------------
load_level:
        dec a                   ; table offset = (level-1)*5 -- in 16
        ld l,a                  ; BITS: 59 entries span 295 bytes, and
        ld h,0                  ; 8-bit maths overflowed at level 53!
        ld e,l
        ld d,h
        add hl,hl               ; x2
        add hl,hl               ; x4
        add hl,de               ; x5
        ld de,level_table
        add hl,de
        ld a,(hl)               ; bank select value
        inc hl
        push af
        ld e,(hl)
        inc hl
        ld d,(hl)               ; DE = source address in the bank
        inc hl
        ld c,(hl)
        inc hl
        ld b,(hl)               ; BC = blob length
        ex de,hl                ; HL = source
        ld de,level_buffer
        pop af
        di
        push bc
        ld b,#7F
        ld c,a
        out (c),c               ; the level's bank over #4000...
        pop bc
        ldir                    ; ...copy the blob down to main RAM...
        ld bc,#7FC0
        out (c),c               ; ...straight 64K again
        ei
        ; --- zone palette: 1-19 mech, 20-39 agri, 40-59 admin
        ld hl,zone_palettes
        ld de,16
        ld a,(current_level)
        cp 20
        jr c,ll_pal
        add hl,de
        cp 40
        jr c,ll_pal
        add hl,de
ll_pal:
        call set_palette
        ; --- wire the pointers into the fresh copy
        ld hl,level_buffer+6    ; map follows the 6-byte header
        ld (current_map),hl
        ld a,(level_buffer+4)   ; header +4: elevator door column
        ld (elevator_col),a     ; (#FF = this level has no lift)
        ld de,level_buffer
        ld hl,(level_buffer)    ; header +0: rects offset
        add hl,de
        ld (current_rects),hl
        ld hl,(level_buffer+2)  ; header +2: enemy list offset
        add hl,de
        ; --- unpack enemy spawns into the runtime pool
        ld a,(hl)               ; A = spawn count
        inc hl
        ld ix,entities
        ld b,MAX_ENTITIES
ll_slot:
        push bc
        or a
        jr z,ll_empty           ; out of spawns: clear remaining slots
        dec a
        ld c,(hl)
        inc hl
        ld (ix+0),c             ; type (RIOT/COAT/THROW)
        ld c,(hl)
        inc hl
        ld (ix+1),c             ; x
        ld c,(hl)
        inc hl
        ld (ix+2),c             ; y
        ld c,(hl)
        inc hl
        ld (ix+3),c             ; patrollers: dir = +speed to start
        ld (ix+4),c             ; speed -- or a thrower's interval
        ld c,(hl)
        inc hl
        ld (ix+5),c             ; xmin -- or a thrower's phase
        ld c,(hl)
        inc hl
        ld (ix+6),c             ; xmax
        ld (ix+7),0             ; animation ticker
        ld (ix+8),0
        ld (ix+9),0             ; (a stale rifle must not carry over)
        push af
        ld a,(ix+0)             ; throwers start their wind-up at the
        cp ET_THROW             ; phase, so barrages don't synchronise
        jr nz,ll_no_throw
        ld a,(ix+5)
        ld (ix+8),a
ll_no_throw:
        pop af
        jr ll_next
ll_empty:
        ld (ix+0),ET_NONE
ll_next:
        pop bc
        ld de,ENT_SIZE
        add ix,de
        djnz ll_slot
        push hl
        call arm_riots          ; rifles from level 10; drone clock
        pop hl
        ; --- and after the enemies: the leaky ceiling pipes
        ld a,(hl)
        inc hl
        ld (leak_count),a
        or a
        jr z,ll_switches        ; no leaks -- but switches, vaults and
        ld b,a                  ; doors still follow in the blob!
        ld ix,leaks
ll_leak:
        ld a,(hl)
        inc hl
        ld (ix+0),a             ; x
        ld a,(hl)
        inc hl
        ld (ix+1),a             ; drip spawn y (below the pipe)
        ld a,(hl)
        inc hl
        ld (ix+2),a             ; colour
        ld a,(hl)
        inc hl
        ld (ix+3),a             ; interval
        ld a,(hl)
        inc hl
        ld (ix+4),a             ; first countdown = phase (staggered)
        ld de,6
        add ix,de
        djnz ll_leak
ll_switches:
        ; --- wall switches: remember their cells for touch detection
        ; and, if already thrown on an earlier visit, show them ON
        ld a,(hl)
        inc hl
        ld (switch_count),a
        or a
        jr z,ll_vaults
        ld b,a
        ld ix,switch_tab
ll_sw:
        ld a,(hl)
        inc hl
        ld (ix+0),a             ; switch id (bit in switch_state)
        ld a,(hl)
        inc hl
        ld (ix+1),a             ; map column
        ld a,(hl)
        inc hl
        ld (ix+2),a             ; map row
        push bc
        push hl
        ld a,(ix+0)
        call switch_bit         ; carry = already pressed
        jr nc,ll_sw_off
        ld d,(ix+1)             ; patch the map: lever shown thrown
        ld e,(ix+2)
        call map_cell_addr
        ld (hl),TILE_SWITCH_ON
ll_sw_off:
        pop hl
        pop bc
        ld de,3
        add ix,de
        djnz ll_sw
ll_vaults:
        ; --- vaults: an OPEN vault (its switch thrown) becomes the
        ; keycard it guards, right in the map.  SEALED ones go into
        ; vault_tab so standing on one can point at its switch.
        xor a
        ld (vault_count),a
        ld a,(hl)
        inc hl
        or a
        jr z,ll_vents           ; zero vaults -- but the vents and the
        ld b,a                  ; door trailer still follow!
ll_va:
        ld a,(hl)
        inc hl
        push bc
        push hl
        call switch_bit         ; is this vault's switch thrown?
        pop hl
        ld a,(hl)
        inc hl
        ld d,a                  ; column
        ld a,(hl)
        inc hl
        ld e,a                  ; row
        ld a,(hl)
        inc hl
        pop bc
        jr c,ll_va_open
        ; sealed: remember the cell and which WAY its switch lies --
        ; bit 7 of the colour byte says the lever waits ABOVE
        push bc
        push hl
        ld c,a                  ; C = colour byte (with the dir bit)
        ld a,(vault_count)
        ld l,a
        add a,a
        add a,l                 ; x3: table stride
        ld hl,vault_tab
        add a,l
        ld l,a
        jr nc,ll_va_s1
        inc h
ll_va_s1:
        ld (hl),d               ; map column
        inc hl
        ld (hl),e               ; map row
        inc hl
        ld a,GLYPH_DOWN         ; switch below: point down...
        bit 7,c
        jr z,ll_va_s2
        ld a,GLYPH_UP           ; ...switch above: point up
ll_va_s2:
        ld (hl),a
        ld hl,vault_count
        inc (hl)
        pop hl
        pop bc
        jr ll_va_next
ll_va_open:
        push bc
        push hl
        and 7                   ; strip the direction bit: the colour
        add a,TILE_KEY_BASE
        push af
        dec e                   ; the safe stays where it is -- its
        call map_cell_addr      ; red key appears ON TOP of it (the
        pop af                  ; cell above is empty by construction)
        ld (hl),a
        inc e
        pop hl
        pop bc
ll_va_next:
        djnz ll_va
ll_vents:
        ; --- steam vents: nozzles that blast on their own clocks,
        ; timed off the 300 Hz interrupt counter (see update_vents)
        ld a,(hl)
        inc hl
        ld (vent_count),a
        or a
        jr z,ll_keys
        ld b,a
        ld ix,vent_tab
ll_vn:
        ld a,(hl)               ; map column ->
        inc hl
        add a,a
        add a,a
        ld (ix+0),a             ; x in bytes
        ld a,(hl)               ; map row ->
        inc hl
        add a,a
        add a,a
        add a,a
        sub 16
        ld (ix+1),a             ; the blast column's top y (16 above)
        ld a,(hl)
        inc hl
        ld (ix+2),a             ; interval (frames)
        ld a,(hl)
        inc hl
        ld (ix+3),a             ; first countdown = phase
        ld de,6
        add ix,de
        djnz ll_vn
ll_keys:
        ; --- the key ledger: every key cell has a global id; keys
        ; taken on an earlier visit are wiped from the map for good
        xor a
        ld (key_count),a
        ld a,(hl)
        inc hl
        ld (key_count),a
        or a
        jr z,ll_doors
        ld b,a
        ld ix,key_tab
llk:
        ld a,(hl)
        inc hl
        ld (ix+0),a             ; global key id
        ld a,(hl)
        inc hl
        ld (ix+1),a             ; map column
        ld a,(hl)
        inc hl
        ld (ix+2),a             ; map row
        push bc
        push hl
        ld a,(ix+0)
        call key_taken
        jr nc,llk_keep
        ld d,(ix+1)             ; already pocketed: the tile goes --
        ld e,(ix+2)             ; but only if it really is a key
        call map_cell_addr      ; (a sealed vault's key never even
        ld a,(hl)               ;  materialised: nothing to wipe)
        cp TILE_KEY_BASE
        jr c,llk_keep
        cp TILE_KEY_BASE+5
        jr nc,llk_keep
        ld (hl),TILE_EMPTY
llk_keep:
        pop hl
        pop bc
        ld de,3
        add ix,de
        djnz llk
ll_doors:
        ; --- trailer: first global door id.  Walk the rects, note each
        ; door's id, and REOPEN any remembered from an earlier visit --
        ; an unlocked door stays unlocked for the whole run.
        ld a,(hl)
        ld (door_base),a
        ld ix,(current_rects)
        ld hl,door_tab
        ld c,0                  ; doors seen so far
lld_scan:
        ld a,(ix+0)
        cp #FF
        jr z,lld_done
        ld a,(ix+4)
        and TYPE_DOOR
        jr z,lld_next
        ld a,(door_base)
        add a,c                 ; this door's id
        ld (hl),a
        inc hl
        push ix
        pop de
        ld (hl),e               ; and its rect address, for the
        inc hl                  ; open-by-address lookup later
        ld (hl),d
        inc hl
        inc c
        push hl
        push bc
        call door_opened        ; carry = opened on an earlier visit
        call c,open_door_rect
        pop bc
        pop hl
lld_next:
        repeat 5
        inc ix
        rend
        jr lld_scan
lld_done:
        ld a,c
        ld (door_count),a
        ; a crate eaten on an earlier visit never re-appears: there is
        ; at most one per level, so find the tile and lift it out
        ld a,(current_level)
        dec a
        ld hl,taken_meds
        call bit_locate
        ld a,(hl)
        and b
        ret z                   ; still out there
        ld hl,level_buffer
        ld bc,768
mkw_scan:
        ld a,(hl)
        cp TILE_MEDKIT
        jr z,mkw_hit
        inc hl
        dec bc
        ld a,b
        or c
        jr nz,mkw_scan
        ret                     ; (no crate on this level after all)
mkw_hit:
        ld (hl),TILE_EMPTY
        ret

; ----------------------------------------------------------------------
; The opened-doors ledger: 128 bits, one per door in the whole shaft.
; ----------------------------------------------------------------------
bit_locate:                     ; A = bit id -> HL += id/8, B = mask
        ld c,a
        and 7
        ld b,a
        inc b
        xor a
        scf
bl_sh:
        rla
        djnz bl_sh
        ld b,a
        ld a,c
        rrca
        rrca
        rrca
        and 31
        add a,l
        ld l,a
        jr nc,bl_nc
        inc h
bl_nc:
        ret

door_opened:                    ; A = door id -> carry if already open
        ld hl,opened_doors
        call bit_locate
        ld a,(hl)
        and b
        ret z                   ; (carry clear: still sealed)
        scf
        ret

door_mark_open:                 ; A = door id: remember it forever
        ld hl,opened_doors
        call bit_locate
        ld a,(hl)
        or b
        ld (hl),a
        ret

key_taken:                      ; A = key id -> carry if pocketed
        ld hl,taken_keys
        call bit_locate
        ld a,(hl)
        and b
        ret z
        scf
        ret

key_mark:                       ; A = key id: gone from the shaft
        ld hl,taken_keys
        call bit_locate
        ld a,(hl)
        or b
        ld (hl),a
        ret

key_find:                       ; cell (D,E) -> carry + A = key id
        ld a,(key_count)
        or a
        ret z
        ld b,a
        ld ix,key_tab
kf_loop:
        ld a,(ix+1)
        cp d
        jr nz,kf_next
        ld a,(ix+2)
        cp e
        jr z,kf_hit
kf_next:
        inc ix
        inc ix
        inc ix
        djnz kf_loop
        or a
        ret
kf_hit:
        ld a,(ix+0)
        scf
        ret

; open_door_rect -- IX = door rect: kill the rect, clear its tiles
open_door_rect:
        ld (ix+4),0
        ld a,(ix+0)
        srl a
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
        ld b,a
odr_clear:
        push bc
        call map_cell_addr
        ld (hl),TILE_EMPTY
        call redraw_cell_both
        pop bc
        inc e
        djnz odr_clear
        ret

; switch_bit -- A = switch id (0-31): carry set if that switch has
; been thrown.  Uses A,B,C,HL.
switch_bit:
        ld c,a
        and 7                   ; bit within the byte
        ld b,a
        inc b
        xor a
        scf
sb_sh:
        rla
        djnz sb_sh
        ld b,a                  ; B = mask
        ld a,c
        rrca
        rrca
        rrca
        and 3                   ; byte index (id/8)
        ld c,a
        ld hl,switch_state
        ld a,l
        add a,c
        ld l,a
        jr nc,sb_nc
        inc h
sb_nc:
        ld a,(hl)
        and b
        ret z                   ; (and cleared carry: not pressed)
        scf
        ret

; ----------------------------------------------------------------------
; enter_level -- place the player at the bottom, reset the sprite
; bookkeeping, paint the room, drop into the game loop.  Entered by
; JP; resets SP so no path can leak stack.
; ----------------------------------------------------------------------
enter_level:
        ld sp,#1000
        xor a                   ; in the shaft itself: no music --
        ld (music_on),a         ; mute B+C once, SFX keeps playing
        ld a,9
        ld e,0
        call psg_write
        ld a,10
        ld e,0
        call psg_write
        ld a,(player_x)
        ld (respawn_x),a        ; where death brings us back
        ld a,(entry_y)          ; 176 climbing up / via lift,
        ld (player_y),a         ; 8 when descending in from above
        ld (respawn_y),a
        xor a
        ld (player_yfrac),a
        ld (last_fall),a
        ld hl,0
        ld (player_vy),hl
        ; is this one of the elevator stops?  then remember the visit
        ld hl,elev_stops
        ld c,0
        ld b,ELEV_COUNT
        ld a,(current_level)
el_vs:
        cp (hl)
        jr z,el_mark
        inc hl
        inc c
        djnz el_vs
        jr el_vs_done
el_mark:
        ld b,c                  ; set bit C of visited_stops
        inc b
        xor a
        scf
el_sh:
        rla
        djnz el_sh
        ld hl,visited_stops
        or (hl)
        ld (hl),a
el_vs_done:
        ; arriving on a ladder? keep climbing; else stand
        ld a,(player_x)
        ld b,a
        ld a,(entry_y)
        ld c,a
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
        ld ix,entities          ; same for every entity slot
        ld hl,entity_prev
        ld b,MAX_ENTITIES
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
        ld de,ENT_SIZE
        add ix,de
        djnz el_dslot
        xor a                   ; fresh combat state
        ld (whip_timer),a
        ld (slide_timer),a
        ld (slide_lock),a
        ld (player_duck),a
        ld (fall_hit),a
        ld (immune_timer),a
        ld hl,whip_prev        ; no stale rope to restore
        ld (hl),a
        ld (whip_prev+5),a
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
        ld a,176                ; in from below: emerge at the floor
        ld (entry_y),a
        jp enter_level          ; X carries over (the ladders line up)

; ----------------------------------------------------------------------
; prev_level -- climbed DOWN off the bottom: the ladders are continuous
; between screens, so we drop into the level below at the top of its
; exit ladder (same column -- that is the generator's alignment rule).
; ----------------------------------------------------------------------
prev_level:
        ld a,(current_level)
        dec a
        ld (current_level),a
        call load_level
        ld a,8                  ; in from above: appear at the top,
        ld (entry_y),a          ; still on the rails
        jp enter_level

game_win:
        ; ==============================================================
        ; THE AIRLOCK -- the ending.  Fifty-nine levels below, the
        ; pumps still hammer.  Up here, the seal breaks.
        ; ==============================================================
        ld sp,#1000
        call music_restart
        ld a,1                  ; ...and for the ending
        ld (music_on),a
        call clear_buffers
        ld hl,txt_end_t
        ld b,18
        ld c,56
        ld e,10
        call both_text
        ld hl,txt_end_1a
        ld b,0
        ld c,88
        ld e,1
        call both_text
        ld hl,txt_end_1b
        ld b,4
        ld c,104
        ld e,2
        call both_text
        ld b,150                ; ~3 s a page, or Fire to hurry
        call ew_wait
        ; --- page 2: outside.  The border itself turns green.
        call clear_buffers
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#52
        out (c),a
        ld hl,txt_end_2t
        ld b,12
        ld c,40
        ld e,9
        call both_text2x
        ld hl,txt_end_2a
        ld b,4
        ld c,88
        ld e,1
        call both_text
        ld hl,txt_end_2b
        ld b,4
        ld c,96
        ld e,1
        call both_text
        ld hl,txt_end_2c
        ld b,0
        ld c,112
        ld e,7
        call both_text
        ld hl,txt_end_2d
        ld b,0
        ld c,120
        ld e,7
        call both_text
        ld b,250
        call ew_wait
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#54
        out (c),a
        ; --- page 3: the end
        call clear_buffers
        ld hl,txt_end_3t
        ld b,12
        ld c,80
        ld e,7
        call both_text2x
        ld hl,txt_credit
        ld b,6
        ld c,120
        ld e,2
        call both_text
        ld hl,score             ; stamp the climb's worth into the line
        ld de,txt_score+6
        ld b,4
gw_sd:
        ld a,(hl)
        add a,'0'
        ld (de),a
        inc hl
        inc de
        djnz gw_sd
        ld hl,txt_score
        ld b,20                 ; 10 chars x 4 bytes, centred
        ld c,144
        ld e,10
        call both_text
        ld hl,txt_end_3b
        ld b,20
        ld c,160
        ld e,6
        call both_text
        ld b,255
        call ew_fire            ; this page waits for Fire
        jp menu_screen

; ew_wait -- run B counts of the ending, THREE frames each: the pages
; linger long enough to read twice; Fire still hurries them along
ew_wait:
        push bc
        ld b,3
ew_w2:
        push bc
        call frame_sync
        call flip_buffers
        call read_input
        call sfx_update
        call update_music
        pop bc
        ld a,(input_new)
        bit INP_FIRE,a
        jr nz,ew_skip
        djnz ew_w2
        pop bc
        djnz ew_wait
        ret
ew_skip:
        pop bc
        ret
ew_fire:                        ; loop until Fire
        push bc
        call frame_sync
        call flip_buffers
        call read_input
        call sfx_update
        call update_music
        pop bc
        ld a,(input_new)
        bit INP_FIRE,a
        ret nz
        jr ew_fire

; both_text / both_text2x -- one line into BOTH screen buffers
both_text:
        ld (bt_ptr),hl
        ld a,(draw_page)
        push af
        ld a,#40
        ld (draw_page),a
        push bc
        push de
        call draw_text
        pop de
        pop bc
        ld a,#C0
        ld (draw_page),a
        ld hl,(bt_ptr)
        call draw_text
        pop af
        ld (draw_page),a
        ret
both_text2x:
        ld (bt_ptr),hl
        ld a,(draw_page)
        push af
        ld a,#40
        ld (draw_page),a
        push bc
        push de
        call draw_text_2x
        pop de
        pop bc
        ld a,#C0
        ld (draw_page),a
        ld hl,(bt_ptr)
        call draw_text_2x
        pop af
        ld (draw_page),a
        ret

; ======================================================================
;
;   THE ELEVATOR NETWORK
;
; Levels 1, 10, 20, 30, 40 and 50 have a lift door on their bottom
; floor.  Stand at the door: Up rides to the next stop ABOVE that you
; have already visited on foot, Down to the next below.  visited_stops
; is a bitmask (bit per stop) marked on arrival at each stop level --
; the lift only goes where the mechanic has already been.
;
; ======================================================================
check_elevator:
        ld a,(elevator_col)
        inc a
        ret z                   ; no lift on this level (#FF)
        ld a,(player_state)
        or a
        ret nz                  ; must be standing...
        ld a,(player_y)
        cp 176
        ret nz                  ; ...on the bottom floor...
        ld a,(elevator_col)
        ld b,a
        ld a,(player_x)
        sub b
        add a,2                 ; ...at the double doors: any overlap
        cp 9                    ; of his body with their 8-byte span
        ret nc
        ld a,(input_new)
        bit INP_UP,a
        jp nz,elevator_up
        bit INP_DOWN,a
        jp nz,elevator_down
        ret

elevator_up:                    ; next visited stop above this one
        call elev_index
eu_scan:
        inc c
        ld a,c
        cp ELEV_COUNT
        ret nc                  ; nothing visited above: door stays shut
        call elev_visited
        jr nc,eu_scan
        jr elev_go
elevator_down:                  ; next visited stop below
        call elev_index
ed_scan:
        ld a,c
        or a
        ret z                   ; already at the bottom terminus
        dec c
        call elev_visited
        jr nc,ed_scan
elev_go:
        ld hl,elev_stops
        ld b,0
        add hl,bc
        ld a,(hl)
        ld (current_level),a
        ld a,SFX_PING           ; ding!
        call sfx_start
        ld a,(current_level)
        call load_level
        ld a,(elevator_col)     ; step out centred between the
        add a,2                 ; destination's double doors
        ld (player_x),a
        ld a,176
        ld (entry_y),a
        jp enter_level

elev_index:                     ; C = index of current_level in the
        ld hl,elev_stops        ; stop table (lifts only exist there)
        ld c,0
        ld a,(current_level)
ei_loop:
        cp (hl)
        ret z
        inc hl
        inc c
        jr ei_loop

; ----------------------------------------------------------------------
; draw_lift_panel -- the floor readout over the lift doors: the level
; number as two seven-segment digits, red like an LED.  Redrawn every
; frame after the sprites, so nothing walking past can wipe it.
; ----------------------------------------------------------------------
draw_lift_panel:
        ld a,(elevator_col)
        inc a
        ret z                   ; no lift on this level
        ld a,(current_level)    ; split into tens and ones ("01" style:
        ld d,0                  ; a real panel keeps its leading zero)
dlp_tens:
        cp 10
        jr c,dlp_have
        sub 10
        inc d
        jr dlp_tens
dlp_have:
        ld e,a
        ld a,(elevator_col)
        ld b,a
        push de
        ld a,d
        call dlp_digit          ; tens at the doors' left edge
        pop de
        ld a,(elevator_col)
        add a,4
        ld b,a
        ld a,e                  ; ones four bytes along
; dlp_digit -- seven-segment digit A at byte column B, over the door
dlp_digit:
        ld hl,seg_font
        add a,l
        ld l,a
        jr nc,$+3
        inc h
        ld a,(hl)               ; gfedcba segment mask
        ld ix,seg_geom
        ld c,7
dlp_seg:
        rra                     ; next segment bit into carry
        jr nc,dlp_next
        push af
        push bc
        ld a,(ix+0)             ; dx from the digit's column
        add a,b
        ld b,a
        ld c,(ix+1)             ; scanline
        ld d,(ix+2)             ; width in bytes
        ld e,(ix+3)             ; height in lines
        ld a,#F0                ; both pixels pen 5: LED red
        call fill_rect
        pop bc
        pop af
dlp_next:
        ld de,4
        add ix,de
        dec c
        jr nz,dlp_seg
        ret

elev_visited:                   ; carry set if stop index C is visited
        ld b,c                  ; A = 1 << C
        inc b
        xor a
        scf
ev_sh:
        rla
        djnz ev_sh
        ld b,a
        ld a,(visited_stops)
        and b
        jr z,ev_no
        scf
        ret
ev_no:
        or a
        ret

; ----------------------------------------------------------------------
; take_fall_hit -- landed from more than one floor up.  A short wince
; (brief red border, crash noise), one life gone, but play CONTINUES
; where you landed -- unlike enemy contact, no respawn.
; ----------------------------------------------------------------------
take_fall_hit:
        xor a
        ld (fall_hit),a
        ld a,SFX_HIT
        call sfx_start
        ld b,14                 ; a short wince, ~0.3s
tfh_loop:
        push bc
        call frame_sync
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#4C                ; red border while it stings
        out (c),a
        call sfx_update
        pop bc
        djnz tfh_loop
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#54
        out (c),a
        jp take_hit             ; one energy point, same as any blow

; ----------------------------------------------------------------------
; take_hit -- any contact damage funnels through here.  One energy
; point behind a 3-second immunity flicker; at zero energy a life goes
; and the tank refills; at zero lives, the shaft wins.
; ----------------------------------------------------------------------
take_hit:
        ld a,(immune_timer)
        or a
        ret nz                  ; still untouchable: no harm done
        ld a,IMMUNE_TIME
        ld (immune_timer),a
        ld a,SFX_HIT
        call sfx_start
        ld hl,player_energy
        dec (hl)
        ret nz                  ; bruised: play on where you stand
        ld (hl),ENERGY_MAX      ; energy spent: that costs a life
        ld hl,game_lives
        dec (hl)
        ret nz
        ld b,50                 ; out of lives: the long red goodbye
go_loop:
        push bc
        call frame_sync
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#4C
        out (c),a
        call sfx_update
        pop bc
        djnz go_loop
        ld bc,GA_PORT+#10
        out (c),c
        ld a,#54
        out (c),a
        jp menu_screen

; ======================================================================
;
;   THE PEOPLE OF THE SHAFT -- the entity system
;
; One 10-byte record per entity (see the constants block).  Types:
;   RIOT  patrols its walkway at half pace, shield up
;   COAT  patrols at full pace, crowbar swinging
;   THROW stands on the platform above and, on a personal timer,
;         drops debris over the edge when the player is near
;   PROJ  the falling debris: constant 3 lines/frame until it meets
;         something solid (or the floor) and shatters
;   DYING a two-frame ghost whose old images get erased from both
;         buffers before the slot frees up (whip kills, debris hits)
;
; ======================================================================
update_entities:
        ld ix,entities
        ld b,MAX_ENTITIES
ue_loop:
        push bc
        ld a,(ix+0)
        or a
        jp z,ue_next
        inc (ix+7)              ; everyone animates
        cp ET_RIOT
        jr z,ue_riot
        cp ET_COAT
        jr z,ue_coat
        cp ET_THROW
        jp z,ue_thrower
        cp ET_PROJ
        jp z,ue_proj
        cp ET_DRIP
        jp z,ue_drip
        cp ET_STEAM
        jp z,ue_steam
        cp ET_BULLET
        jp z,ue_bullet
        cp ET_DRONE
        jp z,ue_drone
        dec (ix+8)              ; ET_DYING: fade out, free the slot
        jp nz,ue_next
        ld (ix+0),ET_NONE
        jp ue_next
ue_riot:
        ld a,(ix+9)             ; armed?  (from level 10 load_level
        or a                    ; arms the first few, later all)
        jr z,ue_riot_walk
        dec (ix+8)              ; his reload clock runs every frame
        jr nz,ue_riot_walk
        ld a,RIOT_RELOAD
        ld (ix+8),a
        call riot_fire
ue_riot_walk:
        ld a,(ix+7)             ; riot gear is heavy: one step in four
        and 3                   ; frames (a quarter of walking pace)
        jp nz,ue_next
        jr ue_patrol
ue_coat:
        ld a,(ix+7)             ; the coat men stride every other frame
        and 1                   ; -- brisk, but you can outrun them
        jp nz,ue_next
ue_patrol:
        ld a,(ix+1)
        add a,(ix+3)            ; x += dir (dir carries the speed)
        ld (ix+1),a
        cp (ix+5)               ; left end of the patrol?
        jr c,ue_turn_r
        jr z,ue_turn_r
        cp (ix+6)               ; right end?
        jr nc,ue_turn_l
        jp ue_next
ue_turn_r:
        ld a,(ix+5)
        ld (ix+1),a
        ld a,(ix+4)
        ld (ix+3),a             ; about face: head right
        jp ue_next
ue_turn_l:
        ld a,(ix+6)
        ld (ix+1),a
        xor a
        sub (ix+4)
        ld (ix+3),a             ; head left
        jp ue_next
ue_thrower:
        dec (ix+8)              ; wind-up timer
        jp nz,ue_next
        ld a,(ix+4)             ; rewind for the next throw
        ld (ix+8),a
        ld a,(player_x)         ; only throws when someone is below
        sub (ix+1)              ; to hit (|dx| < 28 bytes)
        jr nc,ue_th_dx
        neg
ue_th_dx:
        cp 28
        jp nc,ue_next
        call spawn_projectile
        jp ue_next
ue_proj:
        ld a,(ix+2)
        add a,3                 ; falling debris: 3 lines/frame
        ld (ix+2),a
        cp 184
        jr nc,ue_proj_die       ; the floor always stops it
        push ix                 ; did it land on something solid?
        ld b,(ix+1)
        ld c,a
        ld d,SPR_W_BYTES
        ld e,8                  ; debris is 8 lines tall
        call probe_level        ; (trashes IX)
        pop ix
        rra
        jp nc,ue_next
ue_proj_die:
        ld (ix+0),ET_DYING
        ld (ix+8),2
        jp ue_next
ue_steam:
        dec (ix+8)              ; the blast holds, then collapses
        jp nz,ue_next
        ld (ix+0),ET_DYING
        ld (ix+8),2
        jp ue_next
ue_drip:
        ld a,(ix+2)
        add a,2                 ; oil falls lazily, 2 lines/frame
        ld (ix+2),a
        cp 184
        jr nc,ue_proj_die       ; splashes on the floor...
        push ix
        ld b,(ix+1)
        ld c,a
        ld d,SPR_W_BYTES
        ld e,4
        call probe_level        ; ...or on the first slab in the way
        pop ix
        rra
        jr c,ue_proj_die
        jp ue_next
ue_bullet:
        ld a,(ix+1)
        add a,(ix+3)            ; fly on
        ld (ix+1),a
        cp 1
        jr c,ub_die             ; the outer walls always stop it
        cp SCR_W_BYTES-2
        jr nc,ub_die
        push ix                 ; ...and so does any slab or crate
        ld b,a
        ld c,(ix+2)
        ld d,2
        ld e,3
        call probe_level
        pop ix
        rra
        jr c,ub_die
        ld a,(player_x)         ; the man himself: a 2-byte slug
        ld c,a                  ; against his 4-byte body
        ld a,(ix+1)
        inc a
        sub c
        cp 5
        jp nc,ue_next
        ld a,(player_y)
        ld d,a
        add a,SPR_H_LINES
        ld e,a
        ld a,(player_duck)
        or a
        jr z,ub_head
        ld a,d
        add a,8                 ; THE dodge: a duck empties the air
        ld d,a                  ; at gun height
ub_head:
        ld a,(ix+2)
        cp e
        jp nc,ue_next           ; under his feet
        add a,2                 ; the slug's last line
        cp d
        jp c,ue_next            ; over the (ducked) head -- or a jump
        call take_hit           ; immunity-aware; may end the game
ub_die:
        ld (ix+0),ET_DYING
        ld (ix+8),2
        jp ue_next
ue_drone:
        ld a,(ix+7)             ; sideways drift every other frame:
        and 1                   ; a walking man can outrun it
        jr nz,ud_fall
        ld a,(player_x)
        cp (ix+1)
        jr z,ud_fall
        jr c,ud_left
        inc (ix+1)
        jr ud_fall
ud_left:
        dec (ix+1)
ud_fall:
        ld a,(player_y)
        add a,4                 ; it dives for the chest
        cp (ix+2)
        jr z,ud_prox
        jr c,ud_up
        inc (ix+2)
        jr ud_prox
ud_up:
        dec (ix+2)
ud_prox:
        ld a,(player_x)         ; touching him?  then it detonates
        sub (ix+1)
        jr nc,ud_dx
        neg
ud_dx:
        cp SPR_W_BYTES
        jp nc,ue_next
        ld a,(player_y)
        ld d,a
        add a,SPR_H_LINES-1
        ld e,a
        ld a,(ix+2)             ; its 8-line shell against his body
        cp e
        jr c,ud_top
        jp nz,ue_next           ; entirely below him
ud_top:
        add a,7
        cp d
        jp c,ue_next            ; entirely above him
        ld (ix+0),ET_DYING      ; it goes off whether or not the
        ld (ix+8),2             ; flicker shields him
        call take_hit
        jp ue_next
ue_next:
        pop bc
        ld de,ENT_SIZE
        add ix,de
        dec b                   ; (djnz can't reach back this far)
        jp nz,ue_loop
        ret

; ----------------------------------------------------------------------
; update_leaks -- each leaky ceiling pipe counts down its own timer
; and lets a drop go; the drip is an ordinary pool entity from there.
; ----------------------------------------------------------------------
update_leaks:
        ld a,(leak_count)
        or a
        ret z
        ld b,a
        ld ix,leaks
ul_loop:
        dec (ix+4)
        jr nz,ul_next
        ld a,(ix+3)             ; rewind to this pipe's interval
        ld (ix+4),a
        call spawn_drip
ul_next:
        ld de,6
        add ix,de
        djnz ul_loop
        ret
spawn_drip:
        push ix
        push bc
        ld iy,entities
        ld b,MAX_ENTITIES
sdr_loop:
        ld a,(iy+0)
        or a
        jr z,sdr_found
        ld de,ENT_SIZE
        add iy,de
        djnz sdr_loop
        pop bc                  ; pool full: the pipe just glistens
        pop ix
        ret
sdr_found:
        ld (iy+0),ET_DRIP
        ld a,(ix+0)
        ld (iy+1),a             ; the pipe's column
        ld a,(ix+1)
        ld (iy+2),a             ; just below the pipe tile
        ld a,(ix+2)
        ld (iy+9),a             ; colour: 1 = lubricant, 0 = water
        ld (iy+7),0
        pop bc
        pop ix
        ret

; ----------------------------------------------------------------------
; update_vents -- the roadmap's "steam vents on the 300 Hz tick":
; vent clocks advance by the measured delta of frame_ticks (the
; interrupt counter), 6 ticks to a beat, so their timing is anchored
; to the hardware interrupt, not to how long our frame took.
; ----------------------------------------------------------------------
update_vents:
        ld a,(vent_count)
        or a
        ret z
        ld hl,vent_last
        ld a,(frame_ticks)
        ld c,a
        sub (hl)                ; ticks since last look
        ld (hl),c
        ld hl,vent_acc
        add a,(hl)
        ld (hl),a
uv_beats:
        ld a,(vent_acc)
        cp 6
        ret c                   ; less than a whole beat banked
        sub 6
        ld (vent_acc),a
        ld a,(vent_count)
        ld b,a
        ld ix,vent_tab
uv_loop:
        dec (ix+3)
        jr nz,uv_next
        ld a,(ix+2)             ; rewind for the next blast
        ld (ix+3),a
        call spawn_steam
uv_next:
        ld de,6
        add ix,de
        djnz uv_loop
        jr uv_beats

spawn_steam:                    ; a standing blast above vent IX
        push ix
        push bc
        ld iy,entities
        ld b,MAX_ENTITIES
sst_loop:
        ld a,(iy+0)
        or a
        jr z,sst_found
        ld de,ENT_SIZE
        add iy,de
        djnz sst_loop
        pop bc                  ; pool full: the vent just hisses
        pop ix
        ret
sst_found:
        ld (iy+0),ET_STEAM
        ld a,(ix+0)
        ld (iy+1),a
        ld a,(ix+1)
        ld (iy+2),a
        ld (iy+7),0
        ld (iy+8),STEAM_TIME
        pop bc
        pop ix
        ret

; ----------------------------------------------------------------------
; spawn_projectile -- thrower at IX drops debris just below his own
; platform's slab; it then falls onto the walkway underneath.
; ----------------------------------------------------------------------
spawn_projectile:
        push ix
        push bc
        ld iy,entities          ; find a free slot in the pool
        ld b,MAX_ENTITIES
sp_loop:
        ld a,(iy+0)
        or a
        jr z,sp_found
        ld de,ENT_SIZE
        add iy,de
        djnz sp_loop
        pop bc                  ; pool full: no throw this time
        pop ix
        ret
sp_found:
        ld (iy+0),ET_PROJ
        ld a,(ix+1)
        ld (iy+1),a             ; the thrower's column
        ld a,(ix+2)
        add a,24                ; clear of his slab, into the open air
        ld (iy+2),a
        ld (iy+7),0
        pop bc
        pop ix
        ret

; ----------------------------------------------------------------------
; riot_fire -- the armed guard at IX levels his rifle.  He only pulls
; the trigger with the player at his own walkway height, on the side
; he is facing, and far enough out that the shot is honest -- then the
; round leaves at gun height, where a duck (or a jump) clears it.
; ----------------------------------------------------------------------
riot_fire:
        ld a,(player_y)
        sub (ix+2)
        add a,4                 ; |py - gy| <= 4: his walkway
        cp 9
        ret nc
        ld a,(player_x)
        sub (ix+1)
        ret z
        jr c,rf_left
        bit 7,(ix+3)            ; target to the right: only when
        ret nz                  ; marching right
        cp 6
        ret c                   ; point-blank is the shield's job
        ld c,1
        jr rf_shoot
rf_left:
        bit 7,(ix+3)
        ret z
        neg
        cp 6
        ret c
        ld c,#FF
rf_shoot:
        ld iy,entities          ; a slot from the shared pool
        ld b,MAX_ENTITIES
rf_slot:
        ld a,(iy+0)
        or a
        jr z,rf_found
        ld de,ENT_SIZE
        add iy,de
        djnz rf_slot
        ret                     ; pool full: the shot stays chambered
rf_found:
        ld (iy+0),ET_BULLET
        ld (iy+3),c
        ld a,(ix+1)
        add a,c
        add a,c                 ; the muzzle, clear of his own body
        ld (iy+1),a
        ld a,(ix+2)
        add a,5                 ; gun height: a ducked head is under it
        ld (iy+2),a
        ld (iy+7),0
        ret

; ----------------------------------------------------------------------
; update_drones -- from DRONE_LVL up, the shaft dispatches kamikaze
; drones on its own clock.  Every expiry of drone_timer only SOMETIMES
; launches; the odds and the ceiling both climb with the level, from
; one rare visitor at 20 to three aloft near the top.
; ----------------------------------------------------------------------
update_drones:
        ld a,(current_level)
        cp DRONE_LVL
        ret c
        ld hl,drone_timer
        dec (hl)
        ret nz
        call rnd8               ; rewind: 60..187 frames to next try
        and #7F
        add a,60
        ld (hl),a
        ld a,(current_level)    ; launch odds: (level-16)*4 in 256 --
        sub 16                  ; 6% at 20, 37% at 40, 67% at 59
        add a,a
        add a,a
        ld c,a
        call rnd8
        cp c
        ret nc                  ; not this time
        ld a,(current_level)    ; the ceiling: 1 aloft at 20, 2 from
        sub DRONE_LVL           ; 36, 3 from 52
        srl a
        srl a
        srl a
        srl a
        inc a
        ld c,a
        ld l,0                  ; count those already flying
        ld ix,entities
        ld b,MAX_ENTITIES
        ld de,ENT_SIZE
ud_cnt:
        ld a,(ix+0)
        cp ET_DRONE
        jr nz,ud_cn
        inc l
ud_cn:
        add ix,de
        djnz ud_cnt
        ld a,l
        cp c
        ret nc                  ; the sky is full
spawn_drone:                    ; (falls through when it is not)
        ld iy,entities
        ld b,MAX_ENTITIES
sd_loop:
        ld a,(iy+0)
        or a
        jr z,sd_found
        add iy,de               ; DE still holds ENT_SIZE
        djnz sd_loop
        ret                     ; pool busy with rocks and drips
sd_found:
        ld (iy+0),ET_DRONE
        call rnd8
        and 63
        add a,8                 ; anywhere along the open top strip
        ld (iy+1),a
        ld (iy+2),8             ; just under the ceiling
        ld (iy+7),0
        ret

; rnd8 -- 8-bit Galois LFSR (poly #1D), period 255.  Seeded per level
; from the 300 Hz clock, so no two runs share a sky.  Trashes only A.
rnd8:
        ld a,(rnd_state)
        add a,a
        jr nc,rn_store
        xor #1D
rn_store:
        ld (rnd_state),a
        ret

; ----------------------------------------------------------------------
; arm_riots -- called as a level is entered: from BULLET_LVL the first
; riot guard carries a rifle, one more every 8 levels, until all of
; them do.  Also winds the drone dispatcher's clock and seeds its dice.
; ----------------------------------------------------------------------
arm_riots:
        ld a,(frame_ticks)      ; human timing at the keys makes every
        or 1                    ; run's sky its own
        ld (rnd_state),a
        ld a,100
        ld (drone_timer),a
        ld a,(current_level)
        sub BULLET_LVL
        ret c                   ; below 10: nobody shoots
        srl a
        srl a
        srl a
        inc a
        ld c,a                  ; C = rifles to hand out
        ld b,MAX_ENTITIES
        ld ix,entities
        ld de,ENT_SIZE
        ld l,30                 ; stagger the opening volleys
ar_loop:
        ld a,(ix+0)
        cp ET_RIOT
        jr nz,ar_next
        ld (ix+9),1
        ld (ix+8),l
        ld a,l
        add a,40
        ld l,a
        dec c
        ret z                   ; every rifle handed out
ar_next:
        add ix,de
        djnz ar_loop
        ret

; ----------------------------------------------------------------------
; check_enemy_hit -- the player's (duck-aware) box vs every hostile.
; Ducking/sliding empties the TOP half of the player box, so debris
; and swings at head height pass clean over.  Carry set = contact.
; ----------------------------------------------------------------------
check_enemy_hit:
        ld a,(immune_timer)     ; still flickering from the last hit?
        or a
        jr z,ceh_live
        ret                     ; (or a cleared carry: untouchable)
ceh_live:
        ld a,(player_y)
        ld d,a                  ; D = player box top...
        add a,SPR_H_LINES
        ld e,a                  ; E = bottom (exclusive; the feet stay)
        ld a,(player_duck)
        or a
        jr z,ceh_box
        ld a,d
        add a,8                 ; ...raised by 8 when ducking
        ld d,a
ceh_box:
        ld ix,entities
        ld b,MAX_ENTITIES
ceh_loop:
        ld a,(ix+0)
        or a
        jr z,ceh_next
        cp ET_DYING
        jr z,ceh_next           ; the defeated can't hurt you
        cp ET_BULLET
        jr nc,ceh_next          ; bullets and drones judge their own
        cp ET_DRIP              ; contact, duck- and immunity-aware
        jr nz,ceh_solid
        ld a,(ix+9)
        or a
        jr z,ceh_next           ; white drip: only oily water
ceh_solid:
        ld a,(player_x)
        sub (ix+1)
        jr nc,ceh_dx
        neg
ceh_dx:
        cp SPR_W_BYTES          ; |dx| >= 4: no horizontal overlap
        jr nc,ceh_next
        ld a,(ix+0)
        cp ET_PROJ
        jr z,ceh_short
        cp ET_DRIP
        jr z,ceh_short
        ld a,SPR_H_LINES        ; people are full height...
        jr ceh_h
ceh_short:
        ld a,8                  ; ...debris and drips are half
ceh_h:
        ld c,a
        ld a,(ix+2)             ; enemy top vs player bottom
        cp e
        jr nc,ceh_next          ; enemy entirely below the feet
        add a,c
        dec a                   ; enemy's last occupied line
        cp d
        jr c,ceh_next           ; ends above the (ducked) head
        scf                     ; contact
        ret
ceh_next:
        repeat ENT_SIZE
        inc ix
        rend
        djnz ceh_loop
        or a
        ret

; ----------------------------------------------------------------------
; THE WHIP -- the mechanic's cable whip.  whip_box works out how much
; rope fits between the shoulder and the wall; whip_hits snares any
; PERSON whose body crosses the rope line (debris can't be snared).
; ----------------------------------------------------------------------
; whip_box -- the whip's reach as a box B=x C=y D=w E=h, from
; whip_dir and the facing.  Carry clear = jammed against an edge.
;   horizontal: WHIP_LEN x 6 at arm height, from the shoulder
;   up:         2 x 16 straight above the head
;   diagonal:   6 x 14 rising forward at 45 degrees
whip_box:
        ld a,(whip_dir)
        or a
        jr z,lb_horiz
        dec a
        jr z,lb_up
        ; --- diagonal
        ld a,(player_facing)
        or a
        jr nz,lb_diag_l
        ld a,(player_x)
        add a,SPR_W_BYTES
        ld b,a
        ld a,SCR_W_BYTES
        sub b
        jr lb_diag_w
lb_diag_l:
        ld a,(player_x)
        sub 6
        jr nc,lb_dl
        xor a
lb_dl:
        ld b,a
        ld a,(player_x)
        sub b
lb_diag_w:
        or a
        ret z
        cp 6
        jr c,lb_dw
        ld a,6
lb_dw:
        ld d,a
        ld a,(player_y)
        sub 12
        jr nc,lb_dy
        xor a
lb_dy:
        ld c,a
        ld a,(player_y)
        add a,2
        sub c
        ld e,a
        scf
        ret
lb_up:
        ld a,(player_y)
        sub 16
        jr nc,lb_uy
        xor a
lb_uy:
        ld c,a
        ld a,(player_y)
        sub c
        or a
        ret z                   ; head against the top edge
        ld e,a
        ld a,(player_x)
        inc a                   ; the centre columns
        ld b,a
        ld d,2
        scf
        ret
lb_horiz:
        ld a,(player_facing)
        or a
        jr nz,lb_left
        ld a,(player_x)
        add a,SPR_W_BYTES
        ld b,a
        ld a,SCR_W_BYTES
        sub b
        jr lb_clamp
lb_left:
        ld a,(player_x)
        sub WHIP_LEN
        jr nc,lb_lok
        xor a
lb_lok:
        ld b,a
        ld a,(player_x)
        sub b
lb_clamp:
        or a
        ret z
        cp WHIP_LEN
        jr c,lb_w
        ld a,WHIP_LEN
lb_w:
        ld d,a
        ld a,(player_y)
        add a,4                 ; the arm-height band
        ld c,a
        ld e,6
        scf
        ret

whip_hits:
        call whip_box
        ret nc
        ld ix,entities
        ld a,MAX_ENTITIES
        ld (wh_n),a
wh_loop:
        ld a,(ix+0)
        or a
        jr z,wh_next
        cp ET_DRONE
        jr nz,wh_person
        ld a,(whip_dir)         ; a flyer: only the overhead and
        or a                    ; diagonal cracks reach that high
        jr z,wh_next
        jr wh_test
wh_person:
        cp ET_PROJ
        jr nc,wh_next           ; otherwise only people can be snared
wh_test:
        ld a,(ix+1)             ; X overlap with the box
        add a,SPR_W_BYTES-1
        cp b
        jr c,wh_next
        ld a,b
        add a,d
        dec a
        cp (ix+1)
        jr c,wh_next
        ld a,(ix+2)             ; Y overlap
        add a,SPR_H_LINES-1
        cp c
        jr c,wh_next
        ld a,c
        add a,e
        dec a
        cp (ix+2)
        jr c,wh_next
        ld (ix+0),ET_DYING      ; snared!
        ld (ix+8),2
        push bc
        push de
        ld a,1                  ; +1: one fewer between you and the top
        call score_add
        ld a,SFX_KILL
        call sfx_start
        pop de
        pop bc
wh_next:
        repeat ENT_SIZE
        inc ix
        rend
        ld a,(wh_n)
        dec a
        ld (wh_n),a
        jr nz,wh_loop
        ret


; ----------------------------------------------------------------------
; render_entities -- the per-frame draw pass, double-buffer aware:
; restore the background under every OLD image in this buffer (player,
; entities, last frame's rope), then draw everything anew.  DYING
; entities get the restore but no draw -- that is how a snared enemy
; or shattered debris vanishes cleanly from BOTH buffers.
; ----------------------------------------------------------------------
render_entities:
        call erase_player
        ; --- the rope from two frames ago in this buffer, if any
        ld hl,whip_prev        ; 5-byte slots {act,x,y,w,h} per buffer
        ld a,(buf_index)
        ld c,a
        add a,a
        add a,a
        add a,c
        ld c,a
        ld b,0
        add hl,bc
        ld a,(hl)
        or a
        jr z,re_no_rope
        ld (hl),0               ; consumed
        inc hl
        ld b,(hl)
        inc hl
        ld c,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld e,(hl)
        call restore_area
re_no_rope:
        ; --- last frame's hint arrow in this buffer, if any
        ld hl,hint_prev
        ld a,(buf_index)
        ld c,a
        add a,a
        add a,c
        ld c,a
        ld b,0
        add hl,bc
        ld a,(hl)
        or a
        jr z,re_no_hint
        ld (hl),0               ; consumed
        inc hl
        ld b,(hl)
        inc hl
        ld c,(hl)
        ld d,4                  ; one glyph: 4 bytes x 8 lines
        ld e,8
        call restore_area
re_no_hint:
        ; --- restore under every entity's old image
        ld ix,entities
        ld iy,entity_prev
        ld b,MAX_ENTITIES
re_erase:
        ld a,(ix+0)
        or a
        jr z,re_e_next
        push bc
        ld a,(buf_index)        ; this buffer's slot: +0/+2 per entity
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
        ld de,ENT_SIZE
        add ix,de
        ld de,4
        add iy,de
        djnz re_erase
        ; --- draw pass
        ld ix,entities
        ld iy,entity_prev
        ld b,MAX_ENTITIES
re_draw:
        ld a,(ix+0)
        or a
        jr z,re_d_next
        cp ET_DYING
        jr z,re_d_next          ; erased above, never drawn again
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
        call entity_sprite      ; DE = the right frame for this type
        call draw_sprite_8x16
        pop bc
re_d_next:
        ld de,ENT_SIZE
        add ix,de
        ld de,4
        add iy,de
        djnz re_draw
        call draw_player        ; player next-to-last: in front of foes
        ; --- and the rope, while the whip is out
        ; --- the vault hint: an arrow over the head, blinking,
        ; pointing the way to the sealed vault's switch
        ld a,(hint_glyph)
        or a
        jr z,re_no_arrow
        ld a,(frame_ctr)
        and 8                   ; blink: 8 frames on, 8 off
        jr z,re_no_arrow
        ld a,(player_y)
        sub 12
        jr nc,re_ar_y
        xor a
re_ar_y:
        ld c,a
        ld a,(player_x)
        ld b,a
        ld hl,hint_prev         ; remember for this buffer's restore
        ld a,(buf_index)
        ld e,a
        add a,a
        add a,e
        ld e,a
        ld d,0
        add hl,de
        ld (hl),1
        inc hl
        ld (hl),b
        inc hl
        ld (hl),c
        ld a,(hint_glyph)
        ld e,7                  ; bright yellow
        call draw_char_any
re_no_arrow:
        ld a,(whip_timer)
        cp WHIP_TIME-5
        ret c                   ; recoiled: nothing to draw
        call whip_box          ; B,C,D,E = this direction's reach
        ret nc
        ld a,(whip_dir)        ; the side crack swings a whole arc, so
        or a                    ; its restore box is the arc's envelope
        jr nz,wb_keep           ; (restore_area clips to the map)
        ld a,(player_x)
        sub 8
        jr nc,wb_x
        xor a
wb_x:
        ld b,a
        ld a,(player_y)
        sub 8
        jr nc,wb_y
        xor a
wb_y:
        ld c,a
        ld d,22
        ld e,22
wb_keep:
        ld hl,whip_prev        ; remember the whole box for restore
        ld a,(buf_index)
        push bc
        ld c,a
        add a,a
        add a,a
        add a,c
        ld c,a
        push de
        ld d,0
        ld e,c
        add hl,de
        pop de
        pop bc
        ld (hl),1
        inc hl
        ld (hl),b
        inc hl
        ld (hl),c
        inc hl
        ld (hl),d
        inc hl
        ld (hl),e
        ld a,(whip_dir)
        or a
        jp z,whip_arc           ; the side crack: an arc, not a line
        dec a
        jr z,rope_up
        ; --- the diagonal: stepped 1x2 segments climbing forward
        ld a,c
        ld (rp_top),a
        ld a,c
        add a,e
        sub 2
        ld (rp_y),a
        ld a,d
        ld (rp_n),a
        ld a,b                  ; start at the shoulder-side end
        ld (rp_x),a
        ld a,(player_facing)
        or a
        jr z,rd_loop
        ld a,b
        add a,d
        dec a
        ld (rp_x),a
rd_loop:
        ld a,(rp_n)
        or a
        ret z
        dec a
        ld (rp_n),a
        ld a,(rp_x)
        ld b,a
        ld a,(rp_y)
        ld c,a
        ld d,1
        ld e,2
        ld a,#FC                ; pen 7 rope
        call fill_rect
        ld a,(player_facing)
        or a
        ld a,(rp_x)
        jr nz,rd_left
        inc a
        jr rd_sx
rd_left:
        dec a
rd_sx:
        ld (rp_x),a
        ld a,(rp_y)
        sub 2
        ld hl,rp_top
        cp (hl)
        ret c                   ; ran out of sky
        ld (rp_y),a
        jr rd_loop
rope_up:
        ld d,1                  ; a thin line straight up
        ld a,#FC
        jp fill_rect
; ----------------------------------------------------------------------
; whip_arc -- the crack, drawn as an ARC.  The thong starts coiled
; behind the shoulder, unwinds up over the head, then snaps out to
; full reach in front.  whip_tab holds six phases of (fx,fy) segments
; in FORWARD coordinates (fx grows the way we face); #80 ends a phase.
; The hit resolves on the extended phase, so the crack lands where the
; thong is actually drawn.
; ----------------------------------------------------------------------
whip_arc:
        ld a,(whip_timer)
        ld b,a
        ld a,WHIP_TIME
        sub b                   ; A = phase 0..5, 0 = still coiled
        ld hl,whip_tab
        or a
        jr z,wa_seg
        ld b,a
wa_skip:
        ld a,(hl)               ; walk past B whole phases
        inc hl
        cp #80
        jr nz,wa_skip
        djnz wa_skip
wa_seg:
        ld a,(hl)
        cp #80
        ret z                   ; this phase is fully drawn
        inc hl
        ld c,a                  ; C = fx (signed, forward)
        ld a,(hl)
        inc hl
        ld d,a                  ; D = fy (signed, downward)
        push hl
        ld a,(player_facing)
        or a
        ld a,(player_x)
        jr z,wa_fwd
        add a,3                 ; facing left: mirror across the body
        sub c
        jr wa_x
wa_fwd:
        add a,c
wa_x:
        cp SCR_W_BYTES
        jr nc,wa_next           ; off either edge (negatives wrap high)
        ld b,a
        ld a,(player_y)
        add a,d
        cp 198
        jr nc,wa_next
        ld c,a
        ld d,1                  ; one byte, two lines: a fat thong link
        ld e,2
        ld a,#FC                ; pen 7
        call fill_rect
wa_next:
        pop hl
        jr wa_seg

whip_tab:                       ; (fx,fy) pairs, #80 closes each phase
        defb -1,5, -2,4, -3,5, -3,7, -2,8, -1,7, #80
        defb -2,2, -2,0, -1,-2, 0,-4, #80
        defb -1,-3, 1,-5, 3,-6, 5,-5, #80
        defb 3,-4, 5,-2, 7,1, 8,4, #80
        defb 4,6, 5,6, 6,6, 7,6, 8,6, 9,6, 10,6, 11,6, #80
        defb 4,6, 5,6, 6,6, 7,6, #80

; entity_sprite -- DE = sprite frame for the entity at IX
entity_sprite:
        ld a,(ix+0)
        cp ET_COAT
        jr z,es_coat
        jr c,es_riot            ; ET_RIOT
        cp ET_BULLET
        jr z,es_bullet
        cp ET_DRONE
        jr z,es_drone
        cp ET_PROJ
        jr z,es_rock
        cp ET_DRIP
        jr z,es_drip
        cp ET_STEAM
        jr z,es_steam
        ; ET_THROW: arm up briefly after each throw
        ld a,(ix+4)             ; interval - timer = frames since throw
        sub (ix+8)
        cp 16
        ld de,spr_throw_b
        ret c
        ld de,spr_throw_a
        ret
es_rock:
        ld de,spr_rock
        ret
es_bullet:
        ld de,spr_bullet
        ret
es_drone:
        ld de,spr_drone_a
        ld a,(ix+7)
        and 2                   ; the rotors are a blur
        ret z
        ld de,spr_drone_b
        ret
es_drip:
        ld de,spr_drip_red
        ld a,(ix+9)
        or a
        ret nz
        ld de,spr_drip_white
        ret
es_steam:
        ld de,spr_steam_a
        ld a,(ix+7)
        and 4                   ; flicker fast: reads as живой steam
        ret z
        ld de,spr_steam_b
        ret
es_riot:
        ld de,spr_riot_r        ; face the patrol direction
        bit 7,(ix+3)
        jr z,es_walk
        ld de,spr_riot_l
        jr es_walk
es_coat:
        ld de,spr_coat_r
        bit 7,(ix+3)
        jr z,es_walk
        ld de,spr_coat_l
es_walk:
        ld a,(ix+7)
        and 8                   ; stride: swap legs every 8 frames
        ret z
        ld hl,4                 ; facing pair: frame B = stub + 4
        add hl,de
        ex de,hl
        ret

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
        xor a                   ; the hint lives one frame at a time
        ld (hint_glyph),a
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
ck_cell:                        ; keycards and medkits, by tile index
        ld a,d
        cp MAP_W
        ret nc
        ld a,e
        cp MAP_H
        ret nc
        call map_cell_addr      ; (preserves D,E)
        ld a,(hl)
        cp TILE_KEY_BASE
        jr c,ck_not_key
        cp TILE_KEY_BASE+5
        jr nc,ck_not_key
        ; a keycard: its colour is its index
        sub TILE_KEY_BASE
        push hl
        push de
        ld e,a
        ld d,0
        ld hl,keys_held
        add hl,de
        ld a,(hl)
        cp 9                    ; pockets full of this colour? then
        jr nc,ck_key_full       ; the card stays where it lies
        inc (hl)
        pop de
        pop hl
        call key_find           ; this exact key, remembered gone
        push hl                 ; (key_mark re-aims HL at the ledger --
        call c,key_mark         ;  ck_took still needs the map cell!)
        pop hl
        ld a,2                  ; +2 for the pocketed card
        call score_add
        jp ck_took
ck_key_full:
        pop de
        pop hl
        ret
ck_not_key:
        cp TILE_VAULT
        jr nz,ck_not_vault
        ; standing at a sealed vault: find it, point at its switch
        push de
        push bc
        ld a,(vault_count)
        or a
        jr z,ck_va_out
        ld b,a
        ld ix,vault_tab
ck_va_find:
        ld a,(ix+0)
        cp d
        jr nz,ck_va_next
        ld a,(ix+1)
        cp e
        jr z,ck_va_hit
ck_va_next:
        inc ix
        inc ix
        inc ix
        djnz ck_va_find
ck_va_out:
        pop bc
        pop de
        ret
ck_va_hit:
        ld a,(ix+2)             ; the arrow glyph to show
        ld (hint_glyph),a
        jr ck_va_out
ck_not_vault:
        cp TILE_SWITCH_OFF
        jr z,ck_switch
        cp TILE_SWITCH_ON
        jr nz,ck_not_switch
        ; standing at a thrown switch: remind where its vault waits
        push de
        push bc
        call sw_find
        call c,sw_hint
        pop bc
        pop de
        ret
ck_switch:
        ; an unpressed switch: throw it, and point at its vault
        push de
        push bc
        call sw_find
        jr nc,ck_sw_out
        call sw_hint
        ld a,(ix+0)             ; set bit id in switch_state (bit 7 is
        ld c,a                  ; the direction flag: harmless here --
        and 7                   ; the bit maths only reads id & 0x1F)
        ld b,a
        inc b
        xor a
        scf
ck_sw_sh:
        rla
        djnz ck_sw_sh
        ld e,a                  ; E = mask
        ld a,c
        rrca
        rrca
        rrca
        and 3
        ld c,a
        ld b,0
        ld hl,switch_state
        add hl,bc
        ld a,(hl)
        or e
        ld (hl),a
        pop bc
        pop de
        call map_cell_addr      ; flip the lever tile to ON
        ld (hl),TILE_SWITCH_ON
        call redraw_cell_both
        ld a,SFX_PING
        jp sfx_start
ck_sw_out:
        pop bc
        pop de
        ret

sw_find:                        ; cell (D,E) -> carry set + IX = record
        ld a,(switch_count)
        or a
        ret z                   ; (carry already clear)
        ld b,a
        ld ix,switch_tab
swf_loop:
        ld a,(ix+1)
        cp d
        jr nz,swf_next
        ld a,(ix+2)
        cp e
        jr z,swf_hit
swf_next:
        inc ix
        inc ix
        inc ix
        djnz swf_loop
        or a
        ret
swf_hit:
        scf
        ret

sw_hint:                        ; arrow from the record's direction bit
        ld a,GLYPH_UP           ; vault above (the classic stream)...
        bit 7,(ix+0)
        jr z,swh_set
        ld a,GLYPH_DOWN         ; ...or below (climb back down for it)
swh_set:
        ld (hint_glyph),a
        ret

ck_not_switch:
        cp TILE_MEDKIT
        ret nz
        ; a medical crate: 1-4 energy, spill-over banks a life
        push hl
        ld a,(current_level)    ; crates are one-shots: bit per level
        dec a
        ld hl,taken_meds
        call bit_locate
        ld a,(hl)
        or b
        ld (hl),a
        ld a,(frame_ctr)
        and 3
        inc a                   ; 1..4 points, luck of the frame
        ld hl,player_energy
        add a,(hl)
        cp ENERGY_MAX+1
        jr c,ck_med_fits
        sub ENERGY_MAX          ; over the top: one life banked
        ld b,a
        ld a,(game_lives)
        cp 9                    ; (one HUD digit)
        jr nc,ck_med_capped
        inc a
        ld (game_lives),a
ck_med_capped:
        ld a,b
ck_med_fits:
        ld (hl),a
        pop hl
ck_took:
        ld (hl),TILE_EMPTY      ; lift it out of the map RAM...
        call redraw_cell_both   ; ...and off both screen buffers
        ld a,SFX_PING
        jp sfx_start            ; (preserves D,E for the loop)

; ----------------------------------------------------------------------
; check_doors -- pushing sideways into a DOOR rect while holding a
; card?  Then consume the card, kill the rect (type 0 matches nothing
; ever again) and clear the door's tiles from map + both screens.
; The walk itself goes through next frame -- the door is simply gone.
; ----------------------------------------------------------------------
check_doors:
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
        ld a,(ix+4)             ; the door's colour rides in bits 4-6
        rrca
        rrca
        rrca
        rrca
        and 7
        ld e,a
        ld d,0
        ld hl,keys_held
        add hl,de
        ld a,(hl)
        or a
        ret z                   ; no key of THIS colour: stays locked
        dec (hl)
        ld a,3                  ; +3: another lock beaten
        call score_add
        ; find this door's id (by rect address) and remember it open
        push ix
        pop de
        ld hl,door_tab
        ld a,(door_count)
        or a
        jr z,cdo_no_id
        ld b,a
cdo_find_id:
        ld c,(hl)               ; candidate id
        inc hl
        ld a,(hl)
        inc hl
        cp e
        jr nz,cdo_id_next
        ld a,(hl)
        cp d
        jr z,cdo_id_hit
cdo_id_next:
        inc hl
        djnz cdo_find_id
        jr cdo_no_id
cdo_id_hit:
        ld a,c
        call door_mark_open     ; stays open on every future visit
cdo_no_id:
        call open_door_rect
        ld a,SFX_PING
        jp sfx_start

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

; draw_char_any -- as draw_char, but y may sit anywhere: the row
; step uses the wrap-safe walk (the hint arrow floats over the head)
draw_char_any:
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
        ld b,8
dca_row:
        push hl
        ld c,(ix+0)
        inc ix
        repeat 4
        xor a
        rlc c
        jr nc,$+3
        or d
        rlc c
        jr nc,$+3
        or e
        ld (hl),a
        inc hl
        rend
        pop hl
        ld a,h                  ; generic next-line step, wrap-safe
        add a,8
        ld h,a
        and #38
        jr nz,dca_ok
        ld a,l
        add a,#50
        ld l,a
        ld a,h
        adc a,#C0
        ld h,a
dca_ok:
        djnz dca_row
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
; draw_text_narrow -- as draw_text but a 3-byte (6px) step, so a longer
; line fits the 80-byte width.  Glyph ink lives in pixel cols 1-5, so
; the overwritten trailing 2px carry no ink and a 1px gap survives.
draw_text_narrow:
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
        add a,3
        ld b,a
        jr draw_text_narrow
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
; score_add -- A = points.  The score lives as four decimal digits
; (most significant first), so the HUD just draws them; overflow pegs
; at 9999.  Preserves BC/DE (callers are mid-loop with live boxes).
; ----------------------------------------------------------------------
score_add:
        push bc
        push hl
        ld b,4
        ld hl,score+3           ; least significant digit
sa_loop:
        add a,(hl)
        cp 10
        jr c,sa_done
        sub 10
        ld (hl),a
        ld a,1                  ; carry a one leftwards
        dec hl
        djnz sa_loop
        ld hl,score             ; ran off the top: peg 9999
        ld a,9
        ld (hl),a
        inc hl
        ld (hl),a
        inc hl
        ld (hl),a
        inc hl
        ld (hl),a
        pop hl
        pop bc
        ret
sa_done:
        ld (hl),a
        pop hl
        pop bc
        ret

; ----------------------------------------------------------------------
; draw_hud -- keycards held (left, green) and lives (right, red).
; Drawn into the hidden buffer every frame: two glyphs, trivial cost,
; and both buffers stay correct without any dirty-tracking.
; ----------------------------------------------------------------------
draw_hud:
        ld a,GLYPH_KEY          ; one key icon, three coloured counts
        ld b,0
        ld c,0
        ld e,2                  ; neutral steel
        call draw_char
        ld a,(keys_held)
        and #0F
        ld b,4
        ld c,0
        ld e,9                  ; green
        call draw_char
        ld a,(keys_held+1)
        and #0F
        ld b,8
        ld c,0
        ld e,10                 ; cyan
        call draw_char
        ld a,(keys_held+2)
        and #0F
        ld b,12
        ld c,0
        ld e,7                  ; yellow
        call draw_char
        ld a,(keys_held+3)
        and #0F
        ld b,16
        ld c,0
        ld e,1                  ; white
        call draw_char
        ld a,(keys_held+4)
        and #0F
        ld b,20
        ld c,0
        ld e,5                  ; red
        call draw_char
        ; the level number, top centre: "LVL 12"
        ld hl,txt_lvl
        ld b,24
        ld c,0
        ld e,2                  ; the label in steel...
        call draw_text_narrow
        ld a,(current_level)
        ld c,0
hud_tens:
        cp 10
        jr c,hud_tdone
        sub 10
        inc c
        jr hud_tens
hud_tdone:
        push af                 ; A = ones digit
        ld a,c
        or a
        ld a,GLYPH_SPACE        ; no leading zero on levels 1-9
        jr z,hud_tblank
        ld a,c
hud_tblank:
        ld b,33                 ; ...the number in white, tucked in
        ld c,0                  ; narrow so the score keeps its berth
        ld e,1
        call draw_char
        pop af
        ld b,36
        ld c,0
        ld e,1
        call draw_char
        ; the score: four white digits, a breath after the level
        ld hl,score
        ld b,42
hud_sc:
        ld a,(hl)               ; a digit IS its glyph
        push hl
        push bc
        ld c,0
        ld e,1
        call draw_char
        pop bc
        pop hl
        inc hl
        ld a,b
        add a,4
        ld b,a
        cp 58
        jr c,hud_sc
        ld a,GLYPH_BOLT         ; energy...
        ld b,60
        ld c,0
        ld e,7
        call draw_char
        ld a,(player_energy)
        and #0F
        ld b,64
        ld c,0
        ld e,7
        call draw_char
        ld a,GLYPH_HEART        ; ...and lives
        ld b,SCR_W_BYTES-8
        ld c,0
        ld e,5
        call draw_char
        ld a,(game_lives)
        and #0F
        ld b,SCR_W_BYTES-4
        ld c,0
        ld e,5
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
        call music_menu
        ld a,1                  ; music on for the title screen
        ld (music_on),a
        xor a                   ; the attract loop starts on the title
        ld (attract_pg),a
        ld a,250
        ld (attract_t),a
        ld hl,menu_map          ; backdrop straight from main RAM
        ld (current_map),hl
        call ms_pages
ms_loop:
        call frame_sync
        call flip_buffers
        call read_input
        call update_music
        ld hl,frame_ctr
        inc (hl)
        ld hl,attract_t         ; five seconds a side: the title, then
        dec (hl)                ; the exhibits, then the title again
        jr nz,ms_steady
        ld (hl),250
        ld a,(attract_pg)
        xor 1
        ld (attract_pg),a
        call ms_pages
ms_steady:
        ld a,(frame_ctr)        ; blink the prompt every 16 frames
        and 16
        ld e,6                  ; orange on...
        jr nz,ms_blink
        ld e,0                  ; ...black off (opaque glyphs erase)
ms_blink:
        ld hl,txt_press
        ld b,0                  ; 20 chars fills the width exactly
        ld c,120                ; (both pages keep this line clear)
        call draw_text
        ld a,(input_new)        ; Space starts the game from EITHER page
        bit INP_FIRE,a
        jr z,ms_loop
        ; --- new game
        ld a,START_LIVES
        ld (game_lives),a
        xor a
        ld (keys_held),a
        ld (keys_held+1),a
        ld (keys_held+2),a
        ld (keys_held+3),a
        ld (keys_held+4),a
        ld (visited_stops),a    ; the lift knows nothing yet
        ld (switch_state),a     ; every vault sealed again
        ld (switch_state+1),a
        ld (switch_state+2),a
        ld (switch_state+3),a
        ld hl,opened_doors      ; ...every door shut, every key and
        ld b,40                 ; ...crate back in place
ms_cd:                          ; (opened_doors, taken_keys, taken_meds
        ld (hl),a               ;  are adjacent: one 40-byte sweep)
        inc hl
        djnz ms_cd
        ld (immune_timer),a
        ld a,ENERGY_MAX
        ld (player_energy),a
        ld hl,score
        ld (hl),0
        inc hl
        ld (hl),0
        inc hl
        ld (hl),0
        inc hl
        ld (hl),0
        ld a,1
        ld (current_level),a
        ld a,38                 ; the mechanic's post, mid-deck
        ld (player_x),a
        ld a,176
        ld (entry_y),a
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
        ld hl,txt_ctl1          ; controls legend, narrow font
        ld b,3
        ld c,88
        ld e,1                  ; white
        call draw_text_narrow
        ld hl,txt_ctl2
        ld b,3
        ld c,96
        ld e,1
        call draw_text_narrow
        ld hl,txt_ctl3
        ld b,11
        ld c,104
        ld e,1
        call draw_text_narrow
        ld hl,txt_credit_menu
        ld b,0                  ; bottom, near full width at a tight step
        ld c,184
        ld e,2                  ; steel grey
        call draw_text_narrow
        ld b,36                 ; the mechanic, on the crate stack
        ld c,128
        ld de,spr_mech_r
        call draw_sprite_8x16
        ld b,44                 ; a riot guard on the crate, squared up
        ld c,128                ; against our hero -- a face-off diorama
        ld de,spr_riot_l
        jp draw_sprite_8x16

ms_pages:                       ; paint the current side into BOTH
        ld a,SCREEN_B/256       ; buffers, so the flip shows one page
        ld (draw_page),a
        call ms_page1
        ld a,SCREEN_A/256
        ld (draw_page),a
ms_page1:
        ld a,(attract_pg)
        or a
        jp z,draw_menu_page
        ; falls through to the exhibits

; ----------------------------------------------------------------------
; draw_info_page -- the attract loop's other side: every object of the
; shaft with its picture, one museum row each.  Tiles must sit on
; character rows (draw_tile steps a flat +#800), so every exhibit y is
; a multiple of 8; line 120 stays clear for the blinking prompt.
; ----------------------------------------------------------------------
draw_info_page:
        call clear_page
        ld hl,txt_know
        ld b,10
        ld c,0                  ; (text needs character rows, like tiles)
        ld e,7                  ; bright yellow title
        call draw_text
        ld ix,info_tab
        ld b,9                  ; nine tile exhibits...
dip_loop:
        push bc
        ld b,2
        ld c,(ix+1)
        call screen_addr
        ex de,hl
        ld a,(ix+0)
        call draw_tile          ; (trashes A,BC,DE,HL)
        ld l,(ix+2)
        ld h,(ix+3)
        ld b,8
        ld c,(ix+1)
        ld e,1
        push ix                 ; draw_char walks the font through IX
        call draw_text_narrow
        pop ix
        ld de,4
        add ix,de
        pop bc
        djnz dip_loop
        ld b,2                  ; ...and two who move: the kamikaze
        ld c,168                ; drone (whip it upward)...
        ld de,spr_drone_a
        call draw_sprite_8x16
        ld hl,txt_i_drone
        ld b,8
        ld c,168
        ld e,1
        call draw_text_narrow
        ld b,2                  ; ...and the guard who shoots
        ld c,184
        ld de,spr_riot_l
        call draw_sprite_8x16
        ld hl,txt_i_guard
        ld b,8
        ld c,184
        ld e,1
        jp draw_text_narrow

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
;   WHIP : the crack -- noise snap + plunging tone, cut hard
;   KILL : a felled guard -- sinking bass tone over deep noise
;   STEP : a footfall -- DC click at level 8, generators off
;          (step_tick arms one every 5 walking frames, A idle only)
;   RUNG : a hand catching a ladder rail -- a 20 ms metallic tick,
;          same cadence, only while the climb actually moves
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
        jp z,sfx_silence        ; just expired: close the channel
        ld a,(sfx_type)
        dec a
        jr z,sfx_upd_jump
        dec a
        jr z,sfx_upd_hit
        dec a
        jr z,sfx_upd_ping
        dec a
        jp z,sfx_upd_whip
        dec a
        jp z,sfx_upd_kill
        dec a
        jp z,sfx_upd_step
        jp sfx_upd_rung
sfx_upd_ping:
        ; ---- PING: high steady tone (period 40 ~= 1.5kHz), fast fade
        xor a
        ld e,40
        call psg_write          ; R0 = period low
        ld a,1
        ld e,0
        call psg_write          ; R1 = period high
        ld a,8                  ; claim: tone on A (music keeps B+C)
        ld (mix_a),a
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
        ld a,8
        ld (mix_a),a
        ld a,8
        ld e,12                 ; steady volume; the sweep does the work
        jp psg_write
sfx_upd_hit:
        ; ---- HIT: white noise, volume halving with the timer (12->0)
        ld a,6
        ld e,14                 ; deep noise period: metallic rumble
        call psg_write
        ld a,1                  ; claim: noise on A
        ld (mix_a),a
        ld a,(sfx_timer)
        srl a
        ld e,a
        ld a,8
        jp psg_write
sfx_upd_whip:
        ; ---- WHIP: the crack itself -- a bright noise SNAP riding a
        ; tone that plunges 62..190 over 6 frames; volume opens at 15
        ; and is cut hard (15,13,11,9,7,5) -- it stings, then it's gone
        ld a,(sfx_timer)
        ld e,a
        ld a,7
        sub e                   ; 1..6 as the crack unwinds
        add a,a
        add a,a
        add a,a
        add a,a
        add a,a                 ; x32
        add a,30
        ld e,a
        xor a
        call psg_write          ; R0
        ld a,1
        ld e,0
        call psg_write          ; R1
        ld a,6
        ld e,3                  ; tight bright noise: the snap itself
        call psg_write
        xor a                   ; claim tone AND noise on A
        ld (mix_a),a
        ld a,(sfx_timer)
        add a,a
        add a,3                 ; 6..1 -> 15,13,11,9,7,5
        ld e,a
        ld a,8
        jp psg_write
sfx_upd_kill:
        ; ---- KILL: a guard goes down -- bass tone sinking under a
        ; deep noise bed, both on channel A.  Period 320+age*64 climbs
        ; to 1216 (~347Hz down to ~92Hz) across 16 frames.
        ld a,(sfx_timer)        ; 15..1
        ld e,a
        ld a,16
        sub e                   ; age 1..15
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl               ; x64
        ld de,256
        add hl,de
        ld e,l
        xor a
        call psg_write          ; R0 (psg_write keeps HL)
        ld e,h
        ld a,1
        call psg_write          ; R1
        ld a,6
        ld e,20                 ; slow, cavernous noise
        call psg_write
        xor a                   ; claim tone AND noise on A
        ld (mix_a),a
        ld a,(sfx_timer)
        cp 14
        jr c,$+4
        ld a,13                 ; 13,13,13,12,11,...,1: a dying rumble
        ld e,a
        ld a,8
        jp psg_write
sfx_upd_step:
        ; ---- STEP: a footfall -- the demo disc's DC click at half
        ; scale.  Both generators stay off; the level jumping to 8
        ; for one frame and back IS the sound: a 20 ms tap each way
        ld a,9                  ; claim: tone and noise both off
        ld (mix_a),a
        ld a,8
        ld e,8
        jp psg_write
sfx_upd_rung:
        ; ---- RUNG: a hand catching a ladder rail -- where the boot's
        ; click is toneless, this is a 20 ms metallic tick
        xor a
        ld e,100                ; ~625 Hz: well under the ping's chime
        call psg_write          ; R0 tone period low
        ld a,1
        ld e,0
        call psg_write          ; R1 high
        ld a,8                  ; claim: tone on A
        ld (mix_a),a
        ld a,8
        ld e,8
        jp psg_write
sfx_silence:
        ld a,8
        ld e,0
        call psg_write          ; volume hard off
        ld a,9                  ; release channel A entirely
        ld (mix_a),a
        ret

; ----------------------------------------------------------------------
; step_tick -- while he walks (or slides) a deck, arm a footfall every
; STEP_FRAMES frames; on a ladder, a rung tick at the same cadence.
; Ladder motion never sets player_moved, so the climb is read off
; player_y actually changing -- a refused climb_step stays silent.
; Lowest-priority sound: it fires only when channel A is idle, so a
; chirp or a crack is never clipped.  Otherwise the cadence re-arms.
; ----------------------------------------------------------------------
step_tick:
        ld a,(player_state)
        or a                    ; ST_GROUND = 0
        jr z,st_wk
        cp ST_CLIMB
        jr z,st_lad
st_rest:
        ld a,STEP_FRAMES
        ld (step_timer),a
        ret
st_wk:
        ld a,(player_moved)
        or a
        jr z,st_rest
        ld c,SFX_STEP
        jr st_beat
st_lad:
        ld hl,climb_py
        ld a,(player_y)
        cp (hl)
        ld (hl),a
        jr z,st_rest            ; hands resting on the rails
        ld c,SFX_RUNG
st_beat:
        ld hl,step_timer
        dec (hl)
        ret nz
        ld (hl),STEP_FRAMES
        ld a,(sfx_timer)        ; something real is ringing: skip the
        or a                    ; beat, keep the cadence
        ret nz
        ld a,c
        jp sfx_start

sfx_len_tab:
        defb 12,24,8,6,16,2,2   ; frames: JUMP HIT PING WHIP KILL STEP RUNG

; ======================================================================
;
;   AY MUSIC -- two voices on channels B and C, leaving channel A to
;   the sound effects.  One (note,duration) stream per voice, #FF
;   loops it; notes index note_table (1-based, 0 = rest).  Two tunes:
;   the TITLE THEME, whose voices are the same length so the piece
;   repeats exactly every 15 seconds, and the ending's DIRGE, whose
;   two loops have different lengths on purpose -- they drift against
;   each other, so it never quite repeats.
;   The mixer register is written HERE, once a frame, combining the
;   music's tone bits with whatever channel A currently claims
;   (mix_a) -- bit 6 stays 0, or the keyboard dies.
;
; ======================================================================
music_menu:                     ; the title screen's 15-second theme
        ld hl,menu_tb
        ld de,menu_tc
        jr mr_set
music_restart:                  ; the ending's drifting dirge
        ld hl,tune_b
        ld de,tune_c
mr_set:
        ld (mus_b_tune),hl
        ld (mus_b_state),hl
        ld (mus_c_tune),de
        ex de,hl
        ld (mus_c_state),hl
        ld a,1
        ld (mus_b_state+2),a
        ld (mus_c_state+2),a
        ret

update_music:
        ld a,(music_on)         ; the dirge belongs to the title and
        or a                    ; the ending -- the climb itself is
        jr nz,um_play           ; pumps, hisses and your own footsteps
        ld a,(mix_a)
        or #36                  ; B+C muted; SFX keeps channel A
        ld e,a
        ld a,7
        jp psg_write
um_play:
        ld ix,mus_b_state
        ld de,(mus_b_tune)
        ld c,2                  ; R2/R3 tone B, R9 volume
        call mus_channel
        ld ix,mus_c_state
        ld de,(mus_c_tune)
        ld c,4                  ; R4/R5 tone C, R10 volume
        call mus_channel
        ld a,(mix_a)            ; the one true mixer write
        or #30                  ; noise B+C off, tones B+C on
        ld e,a
        ld a,7
        jp psg_write

mus_channel:                    ; IX=state{ptr,dur}, DE=tune, C=reg
        dec (ix+2)
        ret nz                  ; the note still rings
        ld l,(ix+0)
        ld h,(ix+1)
mc_fetch:
        ld a,(hl)
        inc hl
        cp #FF
        jr nz,mc_note
        ld l,e                  ; end of the stream: loop it
        ld h,d
        jr mc_fetch
mc_note:
        ld b,(hl)               ; duration in frames
        inc hl
        ld (ix+0),l
        ld (ix+1),h
        ld (ix+2),b
        or a
        jr nz,mc_play
        push bc                 ; a rest: just close the volume
        ld a,c
        srl a
        add a,8                 ; tone R2/R3 -> vol R9, R4/R5 -> R10
        ld e,0
        call psg_write
        pop bc
        ret
mc_play:
        dec a                   ; 1-based note -> table word
        add a,a
        push de
        ld e,a
        ld d,0
        ld hl,note_table
        add hl,de
        pop de
        ld a,(hl)
        ld (mus_period),a
        inc hl
        ld a,(hl)
        ld (mus_period+1),a
        push bc
        ld a,(mus_period)
        ld e,a
        ld a,c
        call psg_write          ; fine period
        pop bc
        push bc
        ld a,(mus_period+1)
        ld e,a
        ld a,c
        inc a
        call psg_write          ; coarse period
        pop bc
        ld a,c
        srl a
        add a,8                 ; tone R2/R3 -> vol R9, R4/R5 -> R10
        ld e,6                  ; melody murmurs...
        cp 10
        jr nz,mc_vol
        ld e,7                  ; ...the bass a shade louder
mc_vol:
        jp psg_write

; (note_table and the tune streams live above the compiled sprites --
; the sub-#4000 bank is crowded and data doesn't need to be here)

; ======================================================================
; set_palette -- program all 16 inks + border via the Gate Array
;
; In: HL -> 16 colour values (one of the zone_palettes).
; GA commands on port #7Fxx: %00nnnnnn selects pen n (or #10 = border),
; %01cccccc sets that pen to hardware colour c.  The palette values
; already include the %01 command bits.
; ======================================================================
set_palette:
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

clear_page:                     ; blacken just the DRAW buffer
        ld a,(draw_page)
        ld h,a
        ld l,0
        jr cb_one

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

; (Palettes now live in the generated file: zone_palettes, one 16-
; colour set per zone.  Pens 0-3,5,7,8 are shared -- player and UI --
; the rest recolour ladders, crates and deco per zone.)

; ----------------------------------------------------------------------
; Menu / UI strings (ASCII; ascii_to_glyph maps them to the font)
; ----------------------------------------------------------------------
; (the txt_* strings live above the compiled sprites with the music
; data -- the sub-#4000 bank is for code, not prose)
bt_ptr:         defw 0

; ----------------------------------------------------------------------
; Generated data -- tools/level_gen.py emits src/levels.asm:
; tileset, font, pen table, menu backdrop, all level blobs (tilemaps +
; collision rects + drone spawns compiled from the same ASCII source),
; and the bank offset table.
; ----------------------------------------------------------------------
        include "levels.asm"

; (line_offsets lives above the compiled sprites with the other
; read-only tables -- 400 bytes the code bank cannot spare)

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
keys_held:      defs 5,0        ; green, cyan, yellow, white, red
player_energy:  defb ENERGY_MAX
score:          defs 4,0        ; four decimal digits, msd first
immune_timer:   defb 0          ; post-hit invulnerability flicker
current_level:  defb 1
respawn_x:      defb 38         ; where this level was entered
sfx_type:       defb 0          ; active sound effect (0 = none)
sfx_timer:      defb 0          ; frames left on it
step_timer:     defb STEP_FRAMES ; frames to the next footfall/rung
climb_py:       defb 0          ; player_y last frame, for ladder motion
drone_timer:    defb 100        ; frames to the dispatcher's next try
rnd_state:      defb #5A        ; the LFSR's shift register (never 0)
attract_pg:     defb 0          ; menu side showing: 0 title, 1 exhibits
attract_t:      defb 250        ; frames until the page turns (5 s)
mix_a:          defb 9          ; channel A's mixer claim (tone/noise)
music_on:       defb 0          ; theme on the menu, dirge on the ending
mus_b_state:    defs 3,0        ; melody: stream ptr + frames left
mus_c_state:    defs 3,0        ; bass
mus_b_tune:     defw 0          ; loop base per voice (theme or dirge)
mus_c_tune:     defw 0
mus_period:     defw 0

player_facing:  defb 0          ; 0 = right, 1 = left (whip, slide)
player_duck:    defb 0          ; low profile this frame (duck/slide)
player_moved:   defb 0          ; walked/slid this frame (walk anim)
fall_hit:       defb 0          ; landed hard: the loop collects a life
entry_y:        defb 176        ; where the next enter_level places us
respawn_y:      defb 176        ; entry point of this level (respawn)
elevator_col:   defb #FF        ; lift door x on this level (#FF none)
visited_stops:  defb 0          ; bitmask of lift stops reached on foot
switch_state:   defs 4,0        ; 32 vault switches, one bit each
opened_doors:   defs 16,0       ; 128 doors, one bit each: stay open
taken_keys:     defs 16,0       ; 128 key cells: pocketed for good
taken_meds:     defs 8,0        ; one crate per level, bit = level-1
door_base:      defb 0          ; first global door id on this level
door_count:     defb 0
door_tab:       defs 4*3,0      ; per door here: id, rect address
switch_count:   defb 0          ; switches on the current level
switch_tab:     defs 8*3,0      ; per switch: id, map col, map row
slide_timer:    defb 0          ; frames of slide burst left
slide_lock:     defb 0          ; one slide per Down press
whip_timer:    defb 0          ; whip out + recoil countdown
whip_dir:      defb 0          ; 0 side, 1 up, 2 diagonal
whip_prev:     defs 10,0       ; per buffer: {act,x,y,w,h} rope box
wh_n:           defb 0          ; whip_hits' loop counter
rp_x:           defb 0          ; diagonal rope walker
rp_y:           defb 0
rp_n:           defb 0
rp_top:         defb 0
ra_c0:          defb 0          ; restore_area's cell window scratch
ra_c1:          defb 0
ra_r0:          defb 0
ra_r1:          defb 0
ra_map:         defw 0          ; walking pointers: column anchors...
ra_scr:         defw 0
ra_mp:          defw 0          ; ...and the row walkers
ra_sp:          defw 0

leak_count:     defb 0
leaks:          defs MAX_LEAKS*6,0    ; x,y,colour,interval,timer,pad
vent_count:     defb 0
vent_tab:       defs MAX_VENTS*6,0    ; x,blast_y,interval,timer,pad,pad
vent_last:      defb 0          ; frame_ticks at the last vent update
vent_acc:       defb 0          ; banked ticks (6 = one beat)
key_count:      defb 0          ; key cells on this level
key_tab:        defs 8*3,0      ; per key: id, map col, map row
vault_count:    defb 0          ; sealed vaults on this level
vault_tab:      defs 4*3,0      ; per vault: map col, row, arrow glyph
hint_glyph:     defb 0          ; arrow to show this frame (0 = none)
hint_prev:      defs 6,0        ; per buffer: {act,x,y} of the arrow

entities:       defs MAX_ENTITIES*ENT_SIZE,0  ; the runtime pool
entity_prev:    defs MAX_ENTITIES*4,0         ; (x,y) x 2 buffers each

; The live copy of the current level, fetched from its bank.  Map +
; rects + enemy spawns; WRITABLE (keycards/doors edit it) and big
; enough for the largest level with room to grow.
level_buffer:   defs 768,0
prev_pos:       defb 38,176     ; where the player was drawn in buffer B
                defb 38,176     ; ... and in buffer A
key_matrix:     defs 10,#FF     ; raw matrix rows (active low, #FF = idle)

        ; keep everything below the #4000 screen buffer
        assert $ < SCREEN_B

; ----------------------------------------------------------------------
; Compiled sprites -- generated, lives at #8000 (the binary spans the
; #4000-#7FFF gap; harmless: that RAM is screen buffer B, cleared at
; boot, and the loader fills the EXTRA-ram banks before RUNning us).
; ----------------------------------------------------------------------
        include "sprites_c.asm"

; ----------------------------------------------------------------------
; Music data -- read-only streams parked above the compiled sprites,
; below AMSDOS space.  Banking only ever remaps #4000-#7FFF, so this
; RAM is as stable as the code bank.
; ----------------------------------------------------------------------
note_table:                     ; AY periods (1 MHz/16/f), C2 up to C5
        defw 956,902,851,803,758,716,676,638,602,568,536,506
        defw 478,451,426,402,379,358,338,319,301,284,268,253,239
        defw 225,213,201,190,179,169,159,150,142,134,127,119
tune_b:                         ; the melody: sparse, minor, watchful
        defb 0,16, 13,8, 16,8, 18,8, 20,24, 18,8, 16,8, 13,24
        defb 0,8, 16,8, 18,8, 20,8, 23,24, 20,8, 18,8, 16,24
        defb #FF
tune_c:                         ; the bass: the pumps, far below
        defb 1,32, 4,32, 6,32, 8,16, 6,16
        defb 1,32, 4,32, 9,32, 8,16, 4,16
        defb #FF

; The title theme: eight bars of A minor, 125 bpm, quarter = 24 frames.
; A rising-fifth hook walked back down, a relative-major lift, a
; leading-tone cadence -- over a pumping root-fifth eighth-note bass.
; Both voices total 768 frames (15.4 s), so they loop in lockstep.
menu_tb:                        ; the melody, one bar per line
        defb 22,24, 29,24, 27,24, 25,24
        defb 24,24, 25,24, 22,44, 0,4
        defb 22,24, 29,24, 27,24, 25,24
        defb 24,24, 21,24, 22,48
        defb 25,24, 29,24, 32,24, 29,24
        defb 30,24, 29,24, 27,24, 24,24
        defb 22,24, 25,24, 24,24, 21,24
        defb 22,72, 0,24
        defb #FF
menu_tc:                        ; the bass: Am Am F E / C G Am-E Am
        defb 10,12, 17,12, 10,12, 17,12, 10,12, 17,12, 10,12, 17,12
        defb 10,12, 17,12, 10,12, 17,12, 10,12, 17,12, 10,12, 17,12
        defb 6,12, 13,12, 6,12, 13,12, 6,12, 13,12, 6,12, 13,12
        defb 5,12, 12,12, 5,12, 12,12, 5,12, 12,12, 5,12, 12,12
        defb 13,12, 8,12, 13,12, 8,12, 13,12, 8,12, 13,12, 8,12
        defb 15,12, 8,12, 15,12, 8,12, 15,12, 8,12, 15,12, 8,12
        defb 10,12, 17,12, 10,12, 17,12, 5,12, 12,12, 5,12, 12,12
        defb 10,24, 17,24, 10,24, 0,24
        defb #FF

; --- every string the game prints, same reasoning
txt_title:      defb "THE SHAFT",0
txt_tag:        defb "THE TRUTH IS ABOVE",0
txt_press:      defb "PRESS SPACE TO START",0
txt_ctl1:       defb "CURSORS - MOVE AND CLIMB",0
txt_ctl2:       defb "SPACE - JUMP    Z - WHIP",0
txt_ctl3:       defb "DOWN - DUCK - SLIDE",0
txt_credit:     defb "REVIVE8BIT - 2026",0
txt_credit_menu: defb "REVIVE8BIT - 2026 - VASPER",0
txt_end_t:      defb "THE AIRLOCK",0
txt_end_1a:     defb "THE SEAL GRINDS OPEN",0
txt_end_1b:     defb "COLD AIR RUSHES IN",0
txt_end_2t:     defb "OUTSIDE",0
txt_end_2a:     defb "GREEN HILLS TO THE",0
txt_end_2b:     defb "HORIZON  CLEAN AIR",0
txt_end_2c:     defb "THE POISON WAS A LIE",0
txt_end_2d:     defb "THE SHAFT WAS A CAGE",0
txt_end_3t:     defb "THE END",0
txt_end_3b:     defb "PRESS FIRE",0
txt_score:      defb "SCORE 0000",0

txt_lvl:        defb "LVL",0

; The lift's LED readout: segment masks (gfedcba) for 0-9, and each
; segment's box {dx, scanline, w bytes, h lines} over the door at 168.
seg_font:       defb #3F,#06,#5B,#4F,#66,#6D,#7D,#07,#7F,#6F
seg_geom:       defb 0,168,3,1          ; a: top bar
                defb 2,168,1,4          ; b: top right
                defb 2,172,1,4          ; c: bottom right
                defb 0,175,3,1          ; d: bottom bar
                defb 0,172,1,4          ; e: bottom left
                defb 0,168,1,4          ; f: top left
                defb 0,171,3,1          ; g: the crossbar

; --- the attract loop's exhibits: every object, one row each
txt_know:       defb "KNOW YOUR SHAFT",0
txt_i_key:      defb "KEYCARD - OPENS ITS DOOR",0
txt_i_door:     defb "DOOR - MATCH ITS COLOR",0
txt_i_switch:   defb "SWITCH - UNSEALS A VAULT",0
txt_i_vault:    defb "VAULT - HOLDS A RED KEY",0
txt_i_med:      defb "MEDKIT - 1 TO 4 ENERGY",0
txt_i_lift:     defb "LIFT - RIDES KNOWN STOPS",0
txt_i_duct:     defb "DUCT - DUCK OR SLIDE",0
txt_i_drip:     defb "RED DRIP BAD - WHITE OK",0
txt_i_vent:     defb "VENT - DODGE THE STEAM",0
txt_i_drone:    defb "DRONE - WHIP IT UPWARD",0
txt_i_guard:    defb "GUARDS SHOOT - DUCK LOW",0
info_tab:                       ; tile, y, text -- the museum's rows
        defb 10,16
        defw txt_i_key
        defb 56,32
        defw txt_i_door
        defb 23,48
        defw txt_i_switch
        defb 25,64
        defw txt_i_vault
        defb 20,80
        defw txt_i_med
        defb 19,96
        defw txt_i_lift
        defb 43,112
        defw txt_i_duct
        defb 21,136
        defw txt_i_drip
        defb 26,152
        defw txt_i_vent

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
        assert $ < #A600        ; below the AMSDOS work RAM
