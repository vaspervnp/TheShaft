"""z80mini.py -- a Z80 subset big enough to EXECUTE this project's own
code, so verification can run the real assembled binary instead of
reasoning about it.  Only the instructions the sources actually use are
implemented; anything else raises loudly rather than silently doing the
wrong thing.  (Named z80mini, not z80: a PyPI package owns that name.)

    from z80mini import Z80, MEM, load, run
    sym = load('build/demo.bin', 'build/demo.sym')
    run(Z80(), sym['OBJECT_PASS'])
"""
import re

MEM = bytearray(0x10000)


def load(binpath, sympath, org=0x1000):
    sym = {}
    for ln in open(sympath):
        m = re.match(r'(\S+)\s+#([0-9A-F]+)', ln)
        if m:
            sym[m.group(1)] = int(m.group(2), 16)
    code = open(binpath, 'rb').read()
    MEM[org:org + len(code)] = code
    return sym


class Z80:
    def __init__(self):
        self.a = self.b = self.c = self.d = self.e = self.h = self.l = 0
        self.ix = self.iy = 0
        self.sp = 0xBF00
        self.pc = 0
        self.fz = self.fc = self.fs = False
        self.halted = False

    def hl(self): return self.h << 8 | self.l
    def de(self): return self.d << 8 | self.e
    def bc(self): return self.b << 8 | self.c
    def sethl(self, v): self.h, self.l = (v >> 8) & 0xFF, v & 0xFF
    def setde(self, v): self.d, self.e = (v >> 8) & 0xFF, v & 0xFF
    def setbc(self, v): self.b, self.c = (v >> 8) & 0xFF, v & 0xFF

    def push(self, v):
        self.sp = (self.sp - 2) & 0xFFFF
        MEM[self.sp] = v & 0xFF
        MEM[self.sp + 1] = (v >> 8) & 0xFF

    def pop(self):
        v = MEM[self.sp] | MEM[self.sp + 1] << 8
        self.sp = (self.sp + 2) & 0xFFFF
        return v

    def n(self):
        v = MEM[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def nn(self):
        v = MEM[self.pc] | MEM[self.pc + 1] << 8
        self.pc = (self.pc + 2) & 0xFFFF
        return v

    def getr(self, i):
        return [self.b, self.c, self.d, self.e, self.h, self.l,
                MEM[self.hl()], self.a][i]

    def setr(self, i, v):
        v &= 0xFF
        if i == 0: self.b = v
        elif i == 1: self.c = v
        elif i == 2: self.d = v
        elif i == 3: self.e = v
        elif i == 4: self.h = v
        elif i == 5: self.l = v
        elif i == 6: MEM[self.hl()] = v
        else: self.a = v

    def alu(self, op, v):
        if op == 0:
            r = self.a + v; self.fc = r > 255; self.a = r & 0xFF
        elif op == 1:
            r = self.a + v + self.fc; self.fc = r > 255; self.a = r & 0xFF
        elif op == 2:
            r = self.a - v; self.fc = r < 0; self.a = r & 0xFF
        elif op == 3:
            r = self.a - v - self.fc; self.fc = r < 0; self.a = r & 0xFF
        elif op == 4: self.a &= v; self.fc = False
        elif op == 5: self.a ^= v; self.fc = False
        elif op == 6: self.a |= v; self.fc = False
        elif op == 7:
            r = self.a - v; self.fc = r < 0
            self.fz = (r & 0xFF) == 0; self.fs = bool(r & 0x80)
            return
        else:
            raise SystemExit(f'alu {op}')
        self.fz = self.a == 0
        self.fs = bool(self.a & 0x80)

    def cond(self, k):
        return [not self.fz, self.fz, not self.fc, self.fc][k]

    def step(self):
        op = self.n()

        if op in (0xDD, 0xFD):                        # IX / IY prefix
            idx = 'ix' if op == 0xDD else 'iy'
            o2 = self.n()
            v = getattr(self, idx)
            def disp():
                d = self.n()
                return (v + (d - 256 if d > 127 else d)) & 0xFFFF
            if o2 == 0x21: setattr(self, idx, self.nn())
            elif o2 == 0x23: setattr(self, idx, (v + 1) & 0xFFFF)
            elif o2 == 0x2B: setattr(self, idx, (v - 1) & 0xFFFF)
            elif o2 == 0x19: setattr(self, idx, (v + self.de()) & 0xFFFF)
            elif o2 == 0x09: setattr(self, idx, (v + self.bc()) & 0xFFFF)
            elif o2 == 0xE5: self.push(v)
            elif o2 == 0xE1: setattr(self, idx, self.pop())
            elif o2 & 0xC7 == 0x46: self.setr((o2 >> 3) & 7, MEM[disp()])
            elif o2 & 0xF8 == 0x70:
                a = disp(); MEM[a] = self.getr(o2 & 7)
            elif o2 == 0x36:
                a = disp(); MEM[a] = self.n()
            elif o2 & 0xC7 == 0x86: self.alu((o2 >> 3) & 7, MEM[disp()])
            else: raise SystemExit(f'{op:02X} {o2:02X} @{self.pc-2:04X}')
            return

        if op == 0xED:
            o2 = self.n()
            if o2 in (0xB0, 0xA0):                    # LDIR / LDI
                while True:
                    MEM[self.de()] = MEM[self.hl()]
                    self.sethl((self.hl() + 1) & 0xFFFF)
                    self.setde((self.de() + 1) & 0xFFFF)
                    self.setbc((self.bc() - 1) & 0xFFFF)
                    if o2 == 0xA0 or self.bc() == 0:
                        break
            elif o2 == 0x52: self._sbc16(self.de())
            elif o2 == 0x42: self._sbc16(self.bc())
            elif o2 == 0x5A:
                r = self.hl() + self.de() + self.fc
                self.fc = r > 0xFFFF; self.sethl(r & 0xFFFF)
            elif o2 == 0x44: self._neg()
            elif o2 == 0x53: a = self.nn(); MEM[a] = self.e; MEM[a + 1] = self.d
            elif o2 == 0x5B: a = self.nn(); self.e = MEM[a]; self.d = MEM[a + 1]
            elif o2 == 0x43: a = self.nn(); MEM[a] = self.c; MEM[a + 1] = self.b
            elif o2 == 0x4B: a = self.nn(); self.c = MEM[a]; self.b = MEM[a + 1]
            elif o2 in (0x56, 0x46, 0x5E): pass       # IM n
            elif o2 == 0x78: self.a = 0               # IN A,(C): no ports
            elif o2 & 0xC7 == 0x41: pass              # OUT (C),r
            else: raise SystemExit(f'ED {o2:02X} @{self.pc-2:04X}')
            return

        if op == 0xCB:
            o2 = self.n(); i = o2 & 7; b = (o2 >> 3) & 7
            if o2 & 0xC0 == 0x40: self.fz = not (self.getr(i) >> b) & 1
            elif o2 & 0xC0 == 0x80: self.setr(i, self.getr(i) & ~(1 << b))
            elif o2 & 0xC0 == 0xC0: self.setr(i, self.getr(i) | (1 << b))
            elif o2 & 0xF8 == 0x38:                   # SRL
                v = self.getr(i); self.fc = bool(v & 1); v >>= 1
                self.setr(i, v); self.fz = v == 0
            elif o2 & 0xF8 == 0x18:                   # RR
                v = self.getr(i); c = self.fc
                self.fc = bool(v & 1); v = (v >> 1) | (c << 7)
                self.setr(i, v); self.fz = v == 0
            elif o2 & 0xF8 == 0x20:                   # SLA
                v = self.getr(i); self.fc = bool(v & 0x80)
                v = (v << 1) & 0xFF; self.setr(i, v); self.fz = v == 0
            elif o2 & 0xF8 == 0x10:                   # RL
                v = self.getr(i); c = self.fc
                self.fc = bool(v & 0x80); v = ((v << 1) | c) & 0xFF
                self.setr(i, v); self.fz = v == 0
            else: raise SystemExit(f'CB {o2:02X}')
            return

        if op & 0xC0 == 0x40 and op != 0x76:
            self.setr((op >> 3) & 7, self.getr(op & 7)); return
        if op & 0xC7 == 0x06:
            self.setr((op >> 3) & 7, self.n()); return
        if op & 0xC0 == 0x80:
            self.alu((op >> 3) & 7, self.getr(op & 7)); return
        if op & 0xC7 == 0xC6:
            self.alu((op >> 3) & 7, self.n()); return
        if op & 0xC7 == 0x04:
            i = (op >> 3) & 7; v = (self.getr(i) + 1) & 0xFF
            self.setr(i, v); self.fz = v == 0; self.fs = bool(v & 0x80); return
        if op & 0xC7 == 0x05:
            i = (op >> 3) & 7; v = (self.getr(i) - 1) & 0xFF
            self.setr(i, v); self.fz = v == 0; self.fs = bool(v & 0x80); return

        if op == 0x00: return
        if op == 0x76: self.halted = True; return
        if op in (0xF3, 0xFB): return
        if op == 0x01: self.setbc(self.nn()); return
        if op == 0x11: self.setde(self.nn()); return
        if op == 0x21: self.sethl(self.nn()); return
        if op == 0x31: self.sp = self.nn(); return
        if op == 0x03: self.setbc((self.bc() + 1) & 0xFFFF); return
        if op == 0x13: self.setde((self.de() + 1) & 0xFFFF); return
        if op == 0x23: self.sethl((self.hl() + 1) & 0xFFFF); return
        if op == 0x0B: self.setbc((self.bc() - 1) & 0xFFFF); return
        if op == 0x1B: self.setde((self.de() - 1) & 0xFFFF); return
        if op == 0x2B: self.sethl((self.hl() - 1) & 0xFFFF); return
        if op == 0x09: self._addhl(self.bc()); return
        if op == 0x19: self._addhl(self.de()); return
        if op == 0x29: self._addhl(self.hl()); return
        if op == 0x39: self._addhl(self.sp); return
        if op == 0x02: MEM[self.bc()] = self.a; return
        if op == 0x12: MEM[self.de()] = self.a; return
        if op == 0x0A: self.a = MEM[self.bc()]; return
        if op == 0x1A: self.a = MEM[self.de()]; return
        if op == 0x22: a = self.nn(); MEM[a] = self.l; MEM[a + 1] = self.h; return
        if op == 0x2A: a = self.nn(); self.l = MEM[a]; self.h = MEM[a + 1]; return
        if op == 0x32: MEM[self.nn()] = self.a; return
        if op == 0x3A: self.a = MEM[self.nn()]; return
        if op == 0x07:
            self.fc = bool(self.a & 0x80)
            self.a = ((self.a << 1) | (self.a >> 7)) & 0xFF; return
        if op == 0x0F:
            self.fc = bool(self.a & 1)
            self.a = ((self.a >> 1) | (self.a << 7)) & 0xFF; return
        if op == 0x17:
            c = self.fc; self.fc = bool(self.a & 0x80)
            self.a = ((self.a << 1) | c) & 0xFF; return
        if op == 0x1F:
            c = self.fc; self.fc = bool(self.a & 1)
            self.a = ((self.a >> 1) | (c << 7)) & 0xFF; return
        if op == 0x37: self.fc = True; return
        if op == 0x3F: self.fc = not self.fc; return
        if op == 0x2F: self.a ^= 0xFF; return
        if op == 0xEB:
            h, l = self.h, self.l
            self.h, self.l = self.d, self.e
            self.d, self.e = h, l; return
        if op == 0xE9: self.pc = self.hl(); return
        if op == 0xF9: self.sp = self.hl(); return
        if op == 0xC5: self.push(self.bc()); return
        if op == 0xD5: self.push(self.de()); return
        if op == 0xE5: self.push(self.hl()); return
        if op == 0xF5:
            f = ((0x40 if self.fz else 0) | (1 if self.fc else 0)
                 | (0x80 if self.fs else 0))
            self.push(self.a << 8 | f); return
        if op == 0xC1: self.setbc(self.pop()); return
        if op == 0xD1: self.setde(self.pop()); return
        if op == 0xE1: self.sethl(self.pop()); return
        if op == 0xF1:
            v = self.pop(); self.a = v >> 8
            self.fz = bool(v & 0x40); self.fc = bool(v & 1)
            self.fs = bool(v & 0x80); return
        if op == 0x10:                                # DJNZ
            d = self.n(); self.b = (self.b - 1) & 0xFF
            if self.b:
                self.pc = (self.pc + (d - 256 if d > 127 else d)) & 0xFFFF
            return
        if op == 0x18:
            d = self.n()
            self.pc = (self.pc + (d - 256 if d > 127 else d)) & 0xFFFF; return
        if op in (0x20, 0x28, 0x30, 0x38):            # JR cc
            d = self.n()
            if self.cond((op - 0x20) >> 3):
                self.pc = (self.pc + (d - 256 if d > 127 else d)) & 0xFFFF
            return
        if op == 0xC3: self.pc = self.nn(); return
        if op & 0xC7 == 0xC2:                         # JP cc
            t = self.nn(); k = (op >> 3) & 7
            if k >= 4: raise SystemExit(f'JP cc {op:02X} unsupported')
            if self.cond(k): self.pc = t
            return
        if op == 0xCD: t = self.nn(); self.push(self.pc); self.pc = t; return
        if op & 0xC7 == 0xC4:                         # CALL cc
            t = self.nn(); k = (op >> 3) & 7
            if k >= 4: raise SystemExit(f'CALL cc {op:02X} unsupported')
            if self.cond(k): self.push(self.pc); self.pc = t
            return
        if op == 0xC9: self.pc = self.pop(); return
        if op & 0xC7 == 0xC0:                         # RET cc
            k = (op >> 3) & 7
            if k >= 4: raise SystemExit(f'RET cc {op:02X} unsupported')
            if self.cond(k): self.pc = self.pop()
            return
        if op in (0xD3, 0xDB): self.n(); return       # OUT (n),A / IN A,(n)
        raise SystemExit(f'opcode {op:02X} @ {self.pc-1:04X}')

    def _neg(self):
        self.fc = self.a != 0
        self.a = (-self.a) & 0xFF
        self.fz = self.a == 0

    def _addhl(self, v):
        r = self.hl() + v
        self.fc = r > 0xFFFF
        self.sethl(r & 0xFFFF)

    def _sbc16(self, v):
        r = self.hl() - v - self.fc
        self.fc = r < 0
        self.sethl(r & 0xFFFF)
        self.fz = (r & 0xFFFF) == 0


def run(z, addr, max_steps=8_000_000):
    """Call `addr` and return when it RETs, counting instructions."""
    z.pc = addr
    z.push(0xFFFF)
    n = 0
    while z.pc != 0xFFFF:
        z.step()
        n += 1
        if n > max_steps:
            raise SystemExit(f'runaway at {z.pc:04X}')
    return n
