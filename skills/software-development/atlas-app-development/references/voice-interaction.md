# Hands-free voice interaction

Use this pattern when a user asks for a conversational voice loop without repeated start/stop actions.

1. Keep one explicit user gesture to enable the mode and obtain browser microphone permission; do not silently turn on microphone capture on page load.
2. Keep the mode opt-in and persistent only for the current UI session. Show an obvious listening/active state and a single clear control to stop it.
3. Segment utterances by voice activity and a configurable silence window; also impose a maximum capture duration. Ensure the silence window is not considered speech and do not send empty audio as a turn.
4. In hands-free mode, bypass optional per-utterance confirmation only because the user explicitly enabled the mode; transcribe and send each utterance once, with the existing request-id/idempotency protections.
5. Do not reopen the microphone while Atlas is processing a turn or speaking. Resume only after the assistant response/audio finishes; stop or clean up tracks and timers when the user disables the mode or the component unmounts.
6. Test the whole loop: permission/start, speech-to-text, one send, assistant TTS, automatic re-listen, and explicit stop. Build success alone is not proof that this flow works in the running browser.
