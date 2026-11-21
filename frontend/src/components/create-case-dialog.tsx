"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useCompanies, useCreateCase, useCreateCompany } from "@/lib/hooks";

const NEW = "__new__";

export function CreateCaseDialog() {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const companies = useCompanies();
  const createCompany = useCreateCompany();
  const createCase = useCreateCase();

  const [companyId, setCompanyId] = React.useState<string>("");
  const [newCompany, setNewCompany] = React.useState("");
  const [sector, setSector] = React.useState("");
  const [title, setTitle] = React.useState("");
  const [amount, setAmount] = React.useState("");
  const [pending, setPending] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    try {
      let cid = companyId;
      if (companyId === NEW || !companyId) {
        if (!newCompany.trim()) {
          toast.error("Enter a company name.");
          setPending(false);
          return;
        }
        const company = await createCompany.mutateAsync({
          legal_name: newCompany.trim(),
          sector: sector.trim() || undefined,
        });
        cid = company.id;
      }
      const created = await createCase.mutateAsync({
        company_id: cid,
        title: title.trim() || "Working-capital appraisal",
        requested_amount: amount.trim() ? amount.trim() : null,
        currency: "INR",
      });
      toast.success("Case created.");
      setOpen(false);
      router.push(`/cases/${created.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not create the case.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus /> New case
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New credit case</DialogTitle>
          <DialogDescription>
            Open a working-capital appraisal for a borrower company.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="company">Company</Label>
            <select
              id="company"
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="">Select a company…</option>
              {(companies.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_name}
                </option>
              ))}
              <option value={NEW}>+ New company…</option>
            </select>
          </div>
          {companyId === NEW || (!companyId && (companies.data ?? []).length === 0) ? (
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="newco">New company name</Label>
                <Input
                  id="newco"
                  value={newCompany}
                  onChange={(e) => setNewCompany(e.target.value)}
                  placeholder="Acme Textiles Pvt Ltd"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="sector">Sector</Label>
                <Input
                  id="sector"
                  value={sector}
                  onChange={(e) => setSector(e.target.value)}
                  placeholder="Textiles"
                />
              </div>
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="title">Case title</Label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Working-capital appraisal"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="amount">Requested amount (₹)</Label>
            <Input
              id="amount"
              inputMode="numeric"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ""))}
              placeholder="40000000"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Creating…" : "Create case"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
