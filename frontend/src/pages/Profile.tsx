import { useParams } from "react-router-dom";
import { api } from "../api";
import { LEVEL } from "../he";
import { LevelBadge, Loading, Shell, WhatsAppLink, useLoad } from "../ui";

// Personal profile. The server decides who may see it (self, Admin, Youth Manager, or a Day Manager for people
// on a date they manage); anyone else gets "לא נמצא". Back returns to the page the user came from.
interface Profile {
  id: string;
  full_name: string;
  level: "trainee" | "youth" | "mentor" | null;
  home_weekday: number | null;
  home_weekday_name: string | null;
  phone: string | null;
}

export function ProfilePage() {
  const { userId } = useParams();
  const p = useLoad(() => api.get<Profile>(`/api/people/${userId}`), [userId]);
  if (!p.data) return <Shell title="פרופיל" back="/"><Loading error={p.error} /></Shell>;
  const d = p.data;
  return (
    <Shell title={d.full_name} back="/">
      <section className="card stack">
        <div className="row spread">
          <strong className="name">{d.full_name}</strong>
          <LevelBadge level={d.level} />
        </div>
        <span className="muted">רמה: {d.level ? LEVEL[d.level] : "ללא רמה"}</span>
        <span className="muted">{d.home_weekday_name ? `יום קבוע: ${d.home_weekday_name}` : "ללא יום קבוע"}</span>
        {d.phone && (
          <div className="row">
            <span dir="ltr">{d.phone}</span>
            <WhatsAppLink phone={d.phone} name={d.full_name} />
          </div>
        )}
      </section>
    </Shell>
  );
}
