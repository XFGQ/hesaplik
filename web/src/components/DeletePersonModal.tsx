import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { PersonRow } from "../api/types";
import { balanceTone, money, signedMoney } from "../lib/format";
import { useToast } from "../lib/toast";
import ConfirmDeleteModal from "./ConfirmDeleteModal";

type Props = {
  person: PersonRow;
  onClose: () => void;
};

export default function DeletePersonModal({ person, onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const balance = Number(person.balance_try);

  const remove = useMutation({
    mutationFn: () => api.deletePerson(person.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      toast("Kişi silindi", "success");
      onClose();
    },
  });

  return (
    <ConfirmDeleteModal
      title="Kişiyi sil"
      description={
        <>
          <p className="panel-title">Kişi</p>
          <p style={{ margin: "0 0 4px", fontWeight: 600, fontSize: 16 }}>{person.full_name}</p>
          <p
            className={`balance-amount ${balanceTone(person.balance_try)}`}
            style={{ fontSize: 20, margin: 0 }}
          >
            {signedMoney(person.balance_try)}
          </p>
        </>
      }
      warning={
        balance !== 0
          ? `Bu kişinin ${money(person.balance_try)} ${balance > 0 ? "borcu" : "alacağı"} var. Silmek kayıtları defterden kaldırır.`
          : undefined
      }
      onConfirm={() => remove.mutate()}
      onClose={onClose}
      isPending={remove.isPending}
      error={remove.isError ? (remove.error as Error).message : null}
    />
  );
}
