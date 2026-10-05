import { createContext, useContext, useState, ReactNode } from "react";
const labels: Record<string, string> = {
  Cases: "मामले",
  "Chain lookup": "चेन खोज",
  Scenarios: "परिदृश्य",
  "Probe map": "प्रोब मानचित्र",
  "VASP directory": "वीएएसपी सूची",
  Labels: "लेबल",
  Analytics: "विश्लेषण",
  Audit: "ऑडिट",
  Notifications: "सूचनाएँ",
  "Sign out": "साइन आउट",
  Trace: "ट्रेस",
  Attribution: "पहचान",
  Taint: "रंगीन निधि",
  Impact: "प्रभाव",
  Approval: "अनुमोदन",
  Routing: "मार्ग",
  Report: "रिपोर्ट",
  Forecast: "पूर्वानुमान",
  "Case workspace": "मामले का कार्यक्षेत्र",
  "Explain this": "समझाएँ",
  "Time replay": "समय पुनःप्रदर्शन",
};
const Context = createContext({
  language: "en",
  setLanguage: (_: string) => {},
  t: (label: string) => label,
});
export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setValue] = useState(
    localStorage.getItem("sahyog-language") || "en",
  );
  function setLanguage(value: string) {
    setValue(value);
    localStorage.setItem("sahyog-language", value);
  }
  return (
    <Context.Provider
      value={{
        language,
        setLanguage,
        t: (label) => (language === "hi" ? labels[label] || label : label),
      }}
    >
      {children}
    </Context.Provider>
  );
}
export const useLanguage = () => useContext(Context);
export function LanguageToggle() {
  const { language, setLanguage } = useLanguage();
  return (
    <label className="language-toggle">
      Language / भाषा
      <select
        aria-label="Language"
        value={language}
        onChange={(e) => setLanguage(e.target.value)}
      >
        <option value="en">English</option>
        <option value="hi">हिन्दी</option>
      </select>
    </label>
  );
}
