# Slides

Two formats of the same deck — pick whichever fits your delivery setup.

---

## Option A — `index.html` (recommended for the live talk)

A self-contained reveal.js deck. Single file, no install. Just **double-click `index.html`**
to open in any modern browser, or:

```bash
open slides/index.html        # macOS
xdg-open slides/index.html    # Linux
start slides/index.html       # Windows
```

### Keyboard shortcuts during the talk

| Key | Action |
|---|---|
| `→` / `Space` / `N` | Next slide |
| `←` / `P` | Previous slide |
| `F` | Full-screen presenter mode |
| `S` | Open speaker-notes window (opens a second screen — great for two monitors) |
| `B` / `.` | Black out the screen (use during demo so the audience watches your terminal) |
| `?` | Show all keybindings |
| `Esc` | Slide overview (thumbnails) |

### Why reveal.js for the panel

- **Looks polished** out of the box; matches the dark-mode professional vibe.
- **Speaker notes window** opens with `S` — your laptop shows current slide + next slide + notes; the projector shows just the current slide.
- **No internet required** during the talk, except to load reveal.js itself from the CDN. If the venue's WiFi is bad, run a one-time `npm install` and host locally — see "offline mode" below.

### Offline mode (no CDN dependency)

If you want zero-internet during the panel:

```bash
cd slides
npm init -y
npm install reveal.js
# then edit index.html: change CDN paths to ./node_modules/reveal.js/...
```

The simpler offline route — **save the page in Chrome (Cmd-S) → "Webpage, Complete"** —
writes a self-contained HTML+assets bundle that works offline.

---

## Option B — `slides.marp.md` (for Google Slides / Keynote / PowerPoint)

The same content as a Marp markdown file. Marp converts markdown to PPTX, PDF, or HTML.

### Convert to PPTX (then import to Google Slides)

```bash
# install Marp CLI (one-time)
npm install -g @marp-team/marp-cli

# convert to PowerPoint
marp slides/slides.marp.md --pptx -o slides/slides.pptx

# convert to PDF (good fallback for the panel)
marp slides/slides.marp.md --pdf -o slides/slides.pdf

# convert to standalone HTML
marp slides/slides.marp.md --html -o slides/slides-marp.html
```

Then in Google Slides: **File → Import slides → Upload → `slides.pptx`**.

### VS Code preview

Install the **"Marp for VS Code"** extension; open `slides.marp.md`; click the preview
icon in the top-right. Live preview as you edit.

---

## Recommendation for the actual panel

**Use `index.html` on your laptop**, projector cloned to mirror mode. Open it before
the call and tap `F` for full-screen. Have `slides.pdf` (from Option B) saved on your
desktop as a *backup* in case anything weird happens with the browser.

Both paths cover all 27 slides; content is identical.
