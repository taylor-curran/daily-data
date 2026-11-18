# Note reading drill (prototype)

Standalone for now; will move into the app once it's good.

```bash
cd note-drill
mkdir -p models && curl -L -o models/vosk-model-small-en-us-0.15.tar.gz \
  https://ccoreilly.github.io/vosk-browser/models/vosk-model-small-en-us-0.15.tar.gz   # ~40 MB, once
python3 -m http.server 8765   # open http://localhost:8765 in Chrome
```

Voice runs offline with Vosk (`vendor/vosk.js`). Add `?engine=chrome` to use Chrome's speech recognition instead.
