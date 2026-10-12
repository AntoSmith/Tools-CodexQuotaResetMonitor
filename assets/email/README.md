# Original email mascot

These original mint robot illustrations were generated with the built-in
`image_gen.imagegen` tool on 2026-09-22 for this project. They are not a third-party
stock asset or an official OpenAI character. No API key or external image URL is
needed to use the finished files.

| File | Intended use | Size | One play | Total plays |
| --- | --- | --- | --- | --- |
| `uncertain.gif` | Unverified reset clues; thinking pose and question mark | 400 × 240 | 3.6 seconds | 2 |
| `celebrate.gif` | Explicit completed reset; cheerful pose and gentle fireworks | 400 × 240 | 3.6 seconds | 2 |
| `neutral.gif` | Welcome, tests, or neutral updates | 400 × 240 | 3.6 seconds | 2 |

The first frame is a complete illustration. Email clients that disable animation
can show that frame. The same-name PNG is a static fallback. Background: `#f8fbfb`.
Use local CID attachments; do not introduce a tracking image URL.

`*-source.png` preserves the generated artwork and transparency. The GIF build
step crops sprite poses, scales, positions, and encodes them without a runtime
Pillow dependency. To regenerate finished assets in a development environment:

```powershell
python scripts/build-email-animation.py
```

The generated images were originally saved under the Codex generated-image
directory. Their original output filenames, for provenance, were:

- Thinking: `exec-c0232d5c-9f40-4d74-ad76-8609b962a721.png`
- Celebration: `exec-699e1afe-d937-4db8-91ed-8ae83d6482c6.png`
- Neutral: `exec-1f1526bb-c2c1-438d-9248-aa8d6eecc351.png`

## Final generation prompts

### Thinking

Use case: illustration-story. Asset type: original animated email mascot sprite sheet, THREE equally sized panels in one HORIZONTAL row, wide image 1536x1024 if needed with panels aligned. Primary request: an original cute small mint-teal bean-shaped robot assistant, smooth matte clay 3D render, oval dark teal face panel with two little expressive eyes, blush cheeks, short rounded arms. UNCERTAIN / THINKING mood: mascot resting both elbows on the bottom edge of a white email card, one hand under its chin, thoughtful sideways eyes, visible upper body only. The three panels show the SAME mascot, SAME size and camera, slightly different poses: (1) head straight, (2) head gently tilted left, (3) head gently tilted right. Character fills center lower 70% of each panel. A tiny dark teal question mark floats above the mascot in each panel. Scene/backdrop: uniform very pale cool off-white #f8fbfb background, no texture, NO border, no visible panel lines. Each mascot has its own small horizontal ledge in matching light mint #d5eeea. Art style: premium friendly soft toy design, very simple silhouette and polished soft ambient lighting, legible when scaled small. Palette: mint #8cddc8, dark teal #0b655b, pale white, tiny peach blush. Constraints: original character not based on any known branded character; no logo, no lettering besides single question mark; no UI mockup; no caption; no watermark; all subjects fully visible and separated by generous blank space. This must be a finished sprite sheet suitable for looping three subtle poses into a GIF.

### Celebration

The thinking artwork was supplied as the character reference.

Use case: identity-preserve / original animated email mascot sprite sheet. Input image 1 is the character reference. Keep exactly this mint-teal cute little robot design, colors, dark teal face panel, round antenna, peach cheeks, material and soft toy rendering. Create NEW sprite sheet in THREE equal-width panels in one horizontal row, transparent background, no panel lines. Mood HAPPY CELEBRATION. Same robot size and front camera in all panels, full body visible. Panel 1: smiling joyfully, both short arms raised, little feet touching ground. Panel 2: same robot leaping gently, eyes smiling closed, arms up. Panel 3: back on ground, hands raised slightly outwards. In every panel add two small tasteful stylized pastel fireworks behind robot upper left/right, tiny peach/mint/gold confetti; fireworks are calm decorative celebration, not huge explosions, character remains focus. All panels aligned to same ground level, plenty of blank spacing between each sprite so rectangular cropping is easy. No ledge or email UI here; no question marks, no text, no logo, no watermark. Do not include a shadow rectangle, border, checkerboard or gray background. Genuine transparency. Full subjects entirely visible including fireworks. This will be assembled into a subtle GIF in a quota reset notification, so first frame must be a complete balanced illustration.

### Neutral

The thinking artwork was supplied as the character reference.

Use case: identity-preserve. Create one finished raster illustration for an email greeting, on a genuinely transparent background. Input image is character reference only. Keep exactly same original cute mint-teal soft toy robot with dark teal face panel, round antenna, white expressive eyes, peach blush and small rounded arms. Centered full body, at rest, holding one small off-white envelope with pale mint trim against its chest. Friendly calm slight smile, neutral welcome mood. No question mark, no raised arms, no fireworks, no confetti, no celebratory symbols, no lettering or logo. Soft even studio lighting, premium clean simple silhouette; whole character visible, generous transparent space around it. Single pose only, no sprite sheet and no extra characters. It must not suggest a quota reset has happened.
