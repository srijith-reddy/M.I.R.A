# mira/ui/avatar.py
# ==========================================================
# ⚪ MIRA Avatar — Smooth Circle + Wave (No Glow)
# ==========================================================
# A minimalist avatar: single smooth orb and soft waveform.
# The circle changes subtly by mode, with no outer glow.
# ==========================================================

import pygame, math, time, threading

class MiraAvatar:
    def __init__(self, width: int = 500, height: int = 300):
        pygame.init()
        pygame.display.set_caption("MIRA Avatar — Clean Mode")
        self.width, self.height = width, height
        self.screen = pygame.display.set_mode((width, height))
        self.active = False
        self.mode = "idle"
        self.t0 = time.time()

    def set_mode(self, mode: str):
        self.mode = mode

    def start(self):
        self.active = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.active = False
        pygame.quit()

    def _loop(self):
        print("⚪ MIRA avatar running (no glow)")
        clock = pygame.time.Clock()

        while self.active:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.stop()
                    return

            t = time.time() - self.t0
            self.screen.fill((5, 5, 15))  # background

            # 🎨 Color + behavior by mode
            if self.mode == "speaking":
                color = (100, 220, 255)
                r = 80 + 5 * abs(math.sin(t * 4))
                amp = 25
                wave_speed = 6.0
            elif self.mode == "thinking":
                color = (120, 160, 255)
                r = 75
                amp = 12
                wave_speed = 2.5
            elif self.mode == "listening":
                color = (90, 180, 255)
                r = 70
                amp = 8
                wave_speed = 1.5
            else:  # idle
                color = (70, 130, 210)
                r = 65
                amp = 3
                wave_speed = 0.8

            center_x = self.width // 2
            center_y = self.height // 2

            # 🌊 Waveform
            pts = []
            for x in range(self.width):
                y = center_y + math.sin(x * 0.02 + t * wave_speed) * amp
                pts.append((x, y))
            pygame.draw.lines(self.screen, color, False, pts, 3)

            # 🔵 Central orb (clean single circle)
            pygame.draw.circle(self.screen, color, (center_x, center_y), int(r))

            pygame.display.flip()
            clock.tick(60)
