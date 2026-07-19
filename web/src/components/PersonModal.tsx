import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { Person, PersonInput } from "../api/types";
import { useToast } from "../lib/toast";
import Modal from "./Modal";

const BOS: PersonInput = {
  full_name: "",
  phone: "",
  city: "",
  district: "",
  address: "",
  note: "",
};

type Props = {
  person?: Person;
  onClose: () => void;
};

export default function PersonModal({ person, onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();

  const [form, setForm] = useState<PersonInput>(
    person
      ? {
          full_name: person.full_name,
          phone: person.phone ?? "",
          city: person.city ?? "",
          district: person.district ?? "",
          address: person.address ?? "",
          note: person.note ?? "",
        }
      : BOS,
  );

  const save = useMutation({
    mutationFn: () => {
      const body: PersonInput = {
        full_name: form.full_name.trim(),
        phone: form.phone?.trim() || null,
        city: form.city?.trim() || null,
        district: form.district?.trim() || null,
        address: form.address?.trim() || null,
        note: form.note?.trim() || null,
      };
      return person ? api.updatePerson(person.id, body) : api.createPerson(body);
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["person", p.id] });
      toast(person ? "Kişi güncellendi" : "Kişi eklendi", "success");
      onClose();
    },
  });

  function set<K extends keyof PersonInput>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  const valid = form.full_name.trim().length >= 2;

  return (
    <Modal title={person ? "Kişiyi düzenle" : "Yeni kişi"} onClose={onClose}>
      <div className="panel">
        <p className="panel-title">Kimlik</p>
        <label className="field">
          <span>Ad soyad</span>
          <input
            value={form.full_name}
            onChange={(e) => set("full_name", e.target.value)}
            placeholder="Ahmet Yılmaz"
            autoFocus
          />
        </label>
        <label className="field">
          <span>Telefon</span>
          <input
            type="tel"
            inputMode="tel"
            value={form.phone ?? ""}
            onChange={(e) => set("phone", e.target.value)}
            placeholder="0555 111 22 33"
          />
        </label>
      </div>

      <div className="panel">
        <p className="panel-title">Adres</p>
        <div className="grid2">
          <label className="field">
            <span>İlçe</span>
            <input
              value={form.district ?? ""}
              onChange={(e) => set("district", e.target.value)}
              placeholder="Karşıyaka"
            />
          </label>
          <label className="field">
            <span>İl</span>
            <input
              value={form.city ?? ""}
              onChange={(e) => set("city", e.target.value)}
              placeholder="İzmir"
            />
          </label>
        </div>
        <label className="field">
          <span>Açık adres</span>
          <textarea
            value={form.address ?? ""}
            onChange={(e) => set("address", e.target.value)}
            placeholder="Mahalle, sokak, no"
          />
        </label>
      </div>

      <div className="panel">
        <p className="panel-title">Not</p>
        <label className="field">
          <span>Kendine hatırlatma</span>
          <textarea
            value={form.note ?? ""}
            onChange={(e) => set("note", e.target.value)}
            placeholder="Örnek: hasat sonu ödüyor, kardeşi Mehmet'le geliyor"
          />
        </label>
      </div>

      {save.isError && (
        <p className="error" style={{ margin: "0 0 12px" }}>
          {(save.error as Error).message}
        </p>
      )}

      <button
        className="primary"
        disabled={!valid || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? "Kaydediliyor" : "Kaydet"}
      </button>
    </Modal>
  );
}
