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

; Debug fill bytes until the tile renderer lands (both pixels same pen)
FILL_SOLID      equ #0C         ; pen 2 = steel grey
FILL_LADDER     equ #3C         ; pen 6 = rusty orange

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

        ; --- Paint the level geometry into BOTH buffers.  It is static,
        ; so drawing it once is enough; the per-frame code only repairs
        ; the small patch the sprite erase chews out of it.
        ld a,SCREEN_B/256
        ld (draw_page),a
        call draw_geometry
        ld a,SCREEN_A/256
        ld (draw_page),a
        call draw_geometry

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

; ======================================================================
; MAIN GAME LOOP
;
;   frame_sync   wait for the frame flyback (VBLANK)   } once per 1/50s
;   flip         show the buffer we finished drawing   } beam off-screen
;   input        scan keyboard + joystick
;   update       move the world (placeholder for now)
;   erase+draw   render into the buffer now hidden
;
; Rendering happens right after the flip, so we have essentially the
; whole frame (19,968 us) to draw before the next flyback.
; ======================================================================
main_loop:
        call frame_sync         ; sleep until the beam flies back to the top
        call flip_buffers       ; hardware page flip -- tear-free by timing
        call read_input         ; keyboard matrix + joystick -> flag bits
        call update_player      ; walk/climb, filtered through collision
        call erase_player       ; wipe old image (+ repair chewed ladders)
        call draw_player        ; draw the new one
        jr main_loop

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
; update_player -- movement filtered through the collision system
;
; The pattern for every move is TRY-THEN-COMMIT: compute the
; destination, probe the level there, and only write the new position
; back if the probe approves.  The player is never inside a wall, so
; nothing ever needs to be pushed back out.
;
; Horizontal = WALK: one byte (2 px) sideways, cancelled if the
; destination box overlaps any SOLID rect.  Walls, crates and platform
; slabs all block identically -- purely a matter of level data.
;
; Vertical = CLIMB: no free flight any more.  UP/DOWN move only if
; find_ladder approves the destination (aligned with a ladder, whole
; body inside its climb volume), with a 1-byte mount-assist snap.
; Gravity and jumping arrive with the physics pass (Prompt 4) -- until
; then walking off an edge leaves you hovering mid-air.
; ======================================================================
update_player:
        ; ---------------- LEFT ----------------
        ld a,(input_held)
        bit INP_LEFT,a
        jr z,mv_no_left
        ld a,(player_x)
        or a                    ; screen-edge guard
        jr z,mv_no_left
        dec a                   ; candidate X, one byte left
        ld b,a
        ld a,(player_y)
        ld c,a
        ld d,SPR_W_BYTES        ; probe with the full sprite box
        ld e,SPR_H_LINES
        call probe_level
        rra                     ; TYPE_SOLID is bit 0 -> carry
        jr c,mv_no_left         ; something solid there: blocked
        ld hl,player_x
        dec (hl)                ; commit
mv_no_left:
        ; ---------------- RIGHT ----------------
        ld a,(input_held)
        bit INP_RIGHT,a
        jr z,mv_no_right
        ld a,(player_x)
        cp SCR_W_BYTES-SPR_W_BYTES
        jr nc,mv_no_right
        inc a
        ld b,a
        ld a,(player_y)
        ld c,a
        ld d,SPR_W_BYTES
        ld e,SPR_H_LINES
        call probe_level
        rra
        jr c,mv_no_right
        ld hl,player_x
        inc (hl)
mv_no_right:
        ; ---------------- UP (climb) ----------------
        ld a,(input_held)
        bit INP_UP,a
        jr z,mv_no_up
        ld a,(player_y)
        sub 2                   ; climb speed: 2 lines/frame
        jr c,mv_no_up
        ld c,a                  ; C = destination Y
        ld a,(player_x)
        ld b,a
        call find_ladder        ; carry = climbable, A = the ladder's x
        jr nc,mv_no_up
        ld (player_x),a         ; mount assist: snap onto the rails
        ld a,c
        ld (player_y),a
mv_no_up:
        ; ---------------- DOWN (climb) ----------------
        ld a,(input_held)
        bit INP_DOWN,a
        jr z,mv_no_down
        ld a,(player_y)
        add a,2
        ld c,a
        ld a,(player_x)
        ld b,a
        call find_ladder
        jr nc,mv_no_down
        ld (player_x),a
        ld a,c
        ld (player_y),a
mv_no_down:
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
        push bc
        call erase_block_8x16
        pop bc
        ; The erase blindly wipes to pen 0; if the player was on a
        ; ladder it just chewed a hole in it, so repaint any ladder the
        ; block touched.  (Prompt 3 replaces both erase and repair with
        ; a proper "restore background tiles" pass.)
        jp repair_ladders

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
; erase_block_8x16 -- wipe a sprite-sized block back to pen 0
;
; In : B = X in bytes, C = Y in lines
; Once the tilemap renderer exists (Prompt 3) this becomes "restore
; background tiles under the old position" -- same walk, different fill.
; ======================================================================
erase_block_8x16:
        call screen_addr
        ld c,SPR_H_LINES
eb_row:
        push hl
        repeat 4
        ld (hl),0
        inc hl
        rend
        pop hl
        ld a,h                  ; same next-line step as the sprite
        add a,8
        ld h,a
        and #38
        jr nz,eb_same_row
        ld a,l
        add a,#50
        ld l,a
        ld a,h
        adc a,#C0
        ld h,a
eb_same_row:
        dec c
        jr nz,eb_row
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
        ld ix,level_rects
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
        ld ix,level_rects
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
;   LEVEL RENDERING -- placeholder debug fills
;
; Flat colour rectangles so the collision geometry is visible and
; playable *today*.  Prompt 3 swaps all of this for the real 8x8 tile
; renderer; fill_rect itself stays useful forever (HUD bars, wipes).
;
; ======================================================================

; ----------------------------------------------------------------------
; fill_rect -- flood a rectangle of screen bytes with one value
; In : B=x (bytes)  C=y (lines)  D=w (>=1)  E=h (>=1)
;      A = fill byte (normally the same pen in both Mode 0 pixels)
; Out: D preserved, everything else working-trashed.  Draw buffer only.
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

; ----------------------------------------------------------------------
; draw_geometry -- paint every level rect in its debug colour
; (called once per buffer at init; fill_rect leaves IX alone, so the
; list walk needs no saving)
; ----------------------------------------------------------------------
draw_geometry:
        ld ix,level_rects
geo_next:
        ld a,(ix+0)
        cp #FF
        ret z
        ld a,(ix+4)             ; type picks the colour
        cp TYPE_LADDER
        ld a,FILL_SOLID         ; (ld doesn't touch the flags)
        jr nz,geo_fill
        ld a,FILL_LADDER
geo_fill:
        ld b,(ix+0)
        ld d,(ix+1)
        ld c,(ix+2)
        ld e,(ix+3)
        call fill_rect
        repeat 5
        inc ix
        rend
        jr geo_next

; ----------------------------------------------------------------------
; repair_ladders -- repaint any ladder the sprite erase just damaged
; In : B,C = x,y of the erased 8x16 block
; Only ladders can legitimately intersect the player (solids block),
; so only ladders ever need repair.  Repainting the whole ladder is
; simple and bounded (4 bytes x 64 lines); while not climbing this
; costs nothing because no ladder overlaps the block.
; ----------------------------------------------------------------------
repair_ladders:
        ld d,SPR_W_BYTES        ; the erased block as a probe box
        ld e,SPR_H_LINES
        ld ix,level_rects
rl_next:
        ld a,(ix+0)
        cp #FF
        ret z
        ld a,(ix+4)
        cp TYPE_LADDER
        jr nz,rl_skip
        call box_overlap
        jr nc,rl_skip
        push bc                 ; keep the block coords for further tests
        push de
        ld b,(ix+0)             ; repaint this whole ladder
        ld d,(ix+1)
        ld c,(ix+2)
        ld e,(ix+3)
        ld a,FILL_LADDER
        call fill_rect
        pop de
        pop bc
rl_skip:
        repeat 5
        inc ix
        rend
        jr rl_next

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
; level_rects -- static collision geometry for the demo screen
;
; Record: {x, w, y, h, type}  --  x/w in bytes, y/h in lines.
; List ends with x = #FF.  Hand-authored for now; from Prompt 3 on,
; each screen's tilemap generates (or replaces) this.
;
; Authoring rules baked into the collision code:
;  * platform surfaces at EVEN y (the climb step is 2 lines)
;  * a ladder rect is the CLIMB VOLUME: from upper_surface-16 (head
;    room above the slab) down to the lower surface; 4 bytes wide
;  * where a ladder passes THROUGH a slab, split the slab and leave a
;    4-byte gap at the ladder's x
;
; The demo screen: shaft walls both sides, floor, a crate to walk
; into, three platforms connected by three offset ladders -- a small
; zig-zag climb from the machine deck towards the top of the screen.
; ----------------------------------------------------------------------
level_rects:
        ;      x,  w,   y,   h,  type
        defb   0, 80, 192,   8, TYPE_SOLID     ; machine-deck floor
        defb   0,  4,   0, 192, TYPE_SOLID     ; left shaft wall
        defb  76,  4,   0, 192, TYPE_SOLID     ; right shaft wall
        defb  20,  8, 176,  16, TYPE_SOLID     ; crate on the floor
        defb   4, 36, 144,   8, TYPE_SOLID     ; platform 1, left of gap
        defb  44, 32, 144,   8, TYPE_SOLID     ; platform 1, right of gap
        defb   4,  8,  96,   8, TYPE_SOLID     ; platform 2, left of gap
        defb  16, 60,  96,   8, TYPE_SOLID     ; platform 2, right of gap
        defb   4, 56,  48,   8, TYPE_SOLID     ; platform 3, left of gap
        defb  64, 12,  48,   8, TYPE_SOLID     ; platform 3, right of gap
        defb  40,  4, 128,  64, TYPE_LADDER    ; floor      -> platform 1
        defb  12,  4,  80,  64, TYPE_LADDER    ; platform 1 -> platform 2
        defb  60,  4,  32,  64, TYPE_LADDER    ; platform 2 -> platform 3
        defb #FF                               ; end of list

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

player_x:       defb 38         ; byte column (0..76), start mid-deck
player_y:       defb 176        ; scanline: standing on the floor (192-16)
prev_pos:       defb 38,176     ; where the player was drawn in buffer B
                defb 38,176     ; ... and in buffer A
key_matrix:     defs 10,#FF     ; raw matrix rows (active low, #FF = idle)

        ; keep everything below the #4000 screen buffer
        assert $ < SCREEN_B
