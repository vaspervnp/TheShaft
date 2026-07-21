10 ' THE SHAFT - disc loader
20 ' shows the title screen, then Space (or 10 seconds) starts the game
30 ' (kept as plain text; build.sh converts line ends + EOF for AMSDOS)
32 ' BASIC refuses to LOAD binaries below HIMEM ("Memory full"), so
34 ' drop HIMEM under &4000 first - the bank loads land above it
36 MEMORY &3FFF
40 BORDER 0:FOR i=0 TO 15:INK i,0:NEXT:MODE 0
50 LOAD"revive8b.scr",&C000
60 ' inks from docs/revive8b.txt
70 INK 0,0:INK 1,13:INK 2,26:INK 3,15:INK 4,25:INK 5,10:INK 6,3:INK 7,1
80 INK 8,11:INK 9,23:INK 10,6:INK 11,24:INK 12,20:INK 13,16:INK 14,12:INK 15,4
82 ' stash all 59 levels in the extra 64K while the title shows:
83 ' page each 16K bank over &4000, LOAD into it, restore with &C0
84 OUT &7F00,&C4:LOAD"levels0.bin",&4000
86 OUT &7F00,&C5:LOAD"levels1.bin",&4000
88 OUT &7F00,&C6:LOAD"levels2.bin",&4000
89 OUT &7F00,&C0
90 CLEAR INPUT
100 t=TIME
110 IF INKEY(47)<>-1 THEN 140
120 IF TIME-t<3000 THEN 110
130 ' 47 = Space; TIME ticks 300/s so 3000 = 10 seconds
140 BORDER 0:FOR i=0 TO 15:INK i,0:NEXT
150 RUN"shaft.bin
