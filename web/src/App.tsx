import { useCallback, useEffect, useState } from "react";

import {
  api,
  type CurrentUser,
  hasSession,
  setSessionEndHandler,
  WAKE_URL,
} from "./api";
import { Audit } from "./views/Audit";
import { Landing } from "./views/Landing";
import { Playground } from "./views/Playground";
import { Settings } from "./views/Settings";
import { Usage } from "./views/Usage";

const TABS = [
  {
    id: "playground",
    label: "Playground",
    roles: ["admin", "member", "auditor"],
  },
  { id: "usage", label: "Usage", roles: ["admin", "auditor"] },
  { id: "audit", label: "Audit log", roles: ["admin", "auditor"] },
  { id: "settings", label: "Keys and policy", roles: ["admin", "auditor"] },
] as const;
type TabId = (typeof TABS)[number]["id"];

function tabFromHash(): TabId {
  const id = window.location.hash.slice(1);
  return TABS.some((tab) => tab.id === id) ? (id as TabId) : "playground";
}

export function App() {
  const [awake, setAwake] = useState(false);
  const [slow, setSlow] = useState(false);
  const [stuck, setStuck] = useState(false);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [signedIn, setSignedIn] = useState(hasSession);
  const [tab, setTab] = useState<TabId>(tabFromHash);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => setSlow(true), 1500);
    const giveUp = setTimeout(() => setStuck(true), 90_000);
    api.wake(controller.signal).then(
      () => setAwake(true),
      () => undefined,
    );
    return () => {
      controller.abort();
      clearTimeout(timer);
      clearTimeout(giveUp);
    };
  }, []);

  useEffect(() => {
    setSessionEndHandler(() => {
      setSignedIn(false);
      setUser(null);
    });
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!awake || !signedIn) {
      return;
    }
    let current = true;
    api.me().then(
      (me) => current && setUser(me),
      () => current && setSignedIn(false),
    );
    return () => {
      current = false;
    };
  }, [awake, signedIn]);

  const endSession = useCallback(() => {
    void api.logout();
    setSignedIn(false);
    setUser(null);
    window.location.hash = "";
  }, []);

  const waking = !awake && slow && (
    <p className="waking" role="status">
      <span className="waking-dot" aria-hidden="true" />
      <span>
        {stuck
          ? "The server is still not answering. Reload the page; if that does not help, the server may be restarting. Try again in a few minutes."
          : "Waking the server. It runs on a free plan and sleeps when nobody uses it, so the first visit takes up to a minute."}
        {WAKE_URL && (
          <>
            {" "}
            <a href={WAKE_URL} target="_blank" rel="noopener noreferrer">
              Taking too long? Wake it up here
            </a>{" "}
            (opens a tab; this page continues once the server answers).
          </>
        )}
      </span>
    </p>
  );

  if (!signedIn) {
    return (
      <>
        {waking}
        <Landing ready={awake} onSignedIn={() => setSignedIn(true)} />
      </>
    );
  }
  if (!user) {
    return (
      <>
        {waking}
        <p className="loading" role="status">
          Opening your workspace
        </p>
      </>
    );
  }

  const tabs = TABS.filter((item) =>
    (item.roles as readonly string[]).includes(user.role),
  );
  const active = tabs.some((item) => item.id === tab) ? tab : "playground";

  return (
    <div className="shell">
      <header className="topbar">
        <a className="wordmark" href="#playground">
          DocuVault
        </a>
        <nav aria-label="Sections">
          {tabs.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className="tab"
              aria-current={item.id === active ? "page" : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="who">
          <span title={user.email}>
            {user.is_guest ? "Demo sandbox" : user.email}, {user.role}
          </span>
          <button type="button" className="quiet" onClick={endSession}>
            End session
          </button>
        </div>
      </header>
      {user.is_guest && (
        <p className="notice">
          You are in a private sandbox for the made-up company Musterfirma GmbH.
          Usage and requests from before today are sample data. Everything here
          is deleted after 60 minutes.
        </p>
      )}
      <main>
        {active === "playground" && <Playground />}
        {active === "usage" && <Usage />}
        {active === "audit" && <Audit />}
        {active === "settings" && <Settings canEdit={user.role === "admin"} />}
      </main>
    </div>
  );
}
