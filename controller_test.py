#!/usr/bin/env python3
"""Controller diagnostic tool - shows real-time button/axis input."""
import pygame
import sys

def main():
    pygame.init()
    pygame.joystick.init()

    # Create a small window for display
    screen = pygame.display.set_mode((800, 600))
    pygame.display.set_caption("Controller Diagnostic")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 24)

    joysticks = []
    print("Detecting joysticks...")
    for i in range(pygame.joystick.get_count()):
        joy = pygame.joystick.Joystick(i)
        joy.init()
        joysticks.append(joy)
        print(f"  Device {i}: {joy.get_name()}")
        print(f"    Buttons: {joy.get_numbuttons()}")
        print(f"    Axes: {joy.get_numaxes()}")
        print(f"    Hats: {joy.get_numhats()}")

    if not joysticks:
        print("ERROR: No joysticks detected! Plug in your controller.")
        pygame.quit()
        return

    joy = joysticks[0]
    pressed_buttons = set()

    print("\n" + "="*60)
    print("PRESS BUTTONS ON YOUR CONTROLLER")
    print("Watch the window for real-time input detection")
    print("="*60)

    running = True
    while running:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.JOYBUTTONDOWN:
                print(f"BUTTON DOWN: {event.button}")
                pressed_buttons.add(event.button)
            elif event.type == pygame.JOYBUTTONUP:
                print(f"BUTTON UP: {event.button}")
                pressed_buttons.discard(event.button)
            elif event.type == pygame.JOYHATMOTION:
                if event.value != (0, 0):
                    print(f"HAT/D-PAD: {event.value}")
            elif event.type == pygame.JOYAXISMOTION:
                if abs(event.value) > 0.5:
                    axis_names = ["LX", "LY", "RX", "RY", "LT", "RT"]
                    axis_name = axis_names[event.axis] if event.axis < len(axis_names) else f"Axis{event.axis}"
                    print(f"AXIS {event.axis} ({axis_name}): {event.value:.2f}")

        # Draw diagnostics
        screen.fill((0, 0, 0))
        y = 20

        lines = [
            f"Controller: {joy.get_name()}",
            "",
            "PRESSED BUTTONS:",
        ]

        if pressed_buttons:
            for btn in sorted(pressed_buttons):
                lines.append(f"  Button {btn} ◄── PRESS THIS")
        else:
            lines.append("  (None - press buttons on your controller)")

        lines.extend([
            "",
            "REAL-TIME VALUES:",
            f"  Buttons: {joy.get_numbuttons()}",
            f"  Axes: {joy.get_numaxes()} (analog sticks)",
            f"  Hats: {joy.get_numhats()} (D-pad)",
        ])

        # Show analog stick values if present
        if joy.get_numaxes() >= 2:
            lx = joy.get_axis(0)
            ly = joy.get_axis(1)
            if abs(lx) > 0.3 or abs(ly) > 0.3:
                lines.append(f"  Left Stick: X={lx:+.2f} Y={ly:+.2f}")

        if joy.get_numaxes() >= 4:
            rx = joy.get_axis(2)
            ry = joy.get_axis(3)
            if abs(rx) > 0.3 or abs(ry) > 0.3:
                lines.append(f"  Right Stick: X={rx:+.2f} Y={ry:+.2f}")

        # Show D-pad
        if joy.get_numhats() > 0:
            hat = joy.get_hat(0)
            if hat != (0, 0):
                lines.append(f"  D-Pad: {hat}")

        lines.extend([
            "",
            "Press ESC or close window to exit"
        ])

        for line in lines:
            text = font.render(line, True, (0, 255, 0))
            screen.blit(text, (20, y))
            y += 28

        pygame.display.flip()

    pygame.quit()
    print("\nDone! Use the button numbers you saw to update engine/joystick.py")

if __name__ == "__main__":
    main()
