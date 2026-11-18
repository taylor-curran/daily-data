# Note reading drill (prototype)

Standalone for now; will move into the app once it's good. Lives on the `devin/note-drill` branch, not `main`.

## Set up on a new machine

```bash
git clone https://github.com/taylor-curran/daily-data && cd daily-data   # or, in an existing clone: git fetch
git checkout devin/note-drill
cd note-drill
mkdir -p models && curl -L -o models/vosk-model-small-en-us-0.15.tar.gz \
  https://ccoreilly.github.io/vosk-browser/models/vosk-model-small-en-us-0.15.tar.gz   # ~40 MB, once; not in git
```

## Run

```bash
cd note-drill
python3 -m http.server 8765   # open http://localhost:8765 in Chrome, allow the mic
```

Voice runs offline with Vosk (`vendor/vosk.js`). Add `?engine=chrome` to use Chrome's speech recognition instead.
Typing the letter works without the mic.
