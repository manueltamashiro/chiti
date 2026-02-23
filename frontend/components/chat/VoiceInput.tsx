"use client";

import { useState, useRef, useEffect } from "react";

type Props = {
  onTranscript: (text: string) => void;
};

export function VoiceInput({ onTranscript }: Props) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const recRef = useRef<SpeechRecognition | null>(null);

  useEffect(() => {
    const SR =
      (window as unknown as { SpeechRecognition?: typeof SpeechRecognition })
        .SpeechRecognition ??
      (window as unknown as { webkitSpeechRecognition?: typeof SpeechRecognition })
        .webkitSpeechRecognition;
    if (SR) {
      setSupported(true);
      const rec = new SR();
      rec.lang = "en-US";
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      rec.onresult = (e) => {
        const text = e.results[0]?.[0]?.transcript ?? "";
        if (text) onTranscript(text);
      };
      rec.onend = () => setListening(false);
      rec.onerror = () => setListening(false);
      recRef.current = rec;
    }
  }, [onTranscript]);

  if (!supported) return null;

  const toggle = () => {
    if (listening) {
      recRef.current?.stop();
    } else {
      recRef.current?.start();
      setListening(true);
    }
  };

  return (
    <button
      onClick={toggle}
      title={listening ? "Stop recording" : "Voice input (push to talk)"}
      className={`p-2 rounded-lg transition-colors ${
        listening
          ? "bg-rose-600 text-white animate-pulse"
          : "bg-zinc-700 text-zinc-400 hover:text-zinc-200"
      }`}
    >
      {listening ? (
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      ) : (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
        </svg>
      )}
    </button>
  );
}
