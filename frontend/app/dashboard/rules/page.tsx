"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { RulesList } from "@/components/rules/rules-list";
import { RuleEditor } from "@/components/rules/rule-editor";
import { TestModal } from "@/components/rules/test-modal";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { NEW_RULE_TEMPLATE, ruleToYaml, yamlToRule } from "@/lib/rules-yaml";

export default function RulesPage() {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const authToken = token ?? undefined;

  const rulesQuery = useQuery({
    queryKey: ["rules"],
    queryFn: () => api.getRules(authToken),
  });

  const rules = useMemo(() => rulesQuery.data?.items ?? [], [rulesQuery.data]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [createSourceFile, setCreateSourceFile] = useState("pii.yaml");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testOpen, setTestOpen] = useState(false);

  const selectedRule = useMemo(
    () => rules.find((r) => r.id === selectedId) ?? null,
    [rules, selectedId]
  );

  // Reset editor content when selection mode changes. Done during render
  // (React "adjust state when props change" pattern) so in-progress edits
  // are not clobbered by query refetches.
  const selectionKey = creating ? "__create__" : selectedId ?? "__none__";
  const [prevSelectionKey, setPrevSelectionKey] = useState<string | null>(null);
  if (prevSelectionKey !== selectionKey) {
    setPrevSelectionKey(selectionKey);
    setSaveError(null);
    if (creating) {
      setYamlText(ruleToYaml(NEW_RULE_TEMPLATE));
    } else if (selectedId) {
      const rule = rules.find((r) => r.id === selectedId);
      if (rule) setYamlText(ruleToYaml(rule));
    } else {
      setYamlText("");
    }
  }

  const { errors, rule: draft } = useMemo(() => yamlToRule(yamlText), [yamlText]);

  const sourceFiles = useMemo(() => {
    const set = new Set(rules.map((r) => r.source_file));
    set.add("pii.yaml");
    set.add("secrets.yaml");
    return Array.from(set).sort();
  }, [rules]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("Fix validation errors before saving");
      const payload = { ...draft };
      if (creating) {
        return api.createRule(payload, createSourceFile, authToken);
      }
      if (!selectedId) throw new Error("Select a rule to update");
      return api.saveRule(selectedId, payload, authToken);
    },
    onSuccess: (rule) => {
      setSaveError(null);
      setCreating(false);
      setSelectedId(rule.id);
      // Keep the query cache in sync so the render-time editor sync below
      // sees the saved rule immediately (invalidate refreshes in background).
      queryClient.setQueryData<{ items: typeof rules; total: number }>(
        ["rules"],
        (old) => {
          if (!old) return old;
          const exists = old.items.some((r) => r.id === rule.id);
          return {
            ...old,
            items: exists
              ? old.items.map((r) => (r.id === rule.id ? rule : r))
              : [...old.items, rule],
            total: exists ? old.total : old.total + 1,
          };
        }
      );
      queryClient.invalidateQueries({ queryKey: ["rules"] });
    },
    onError: (e) => {
      setSaveError(e instanceof Error ? e.message : String(e));
    },
  });

  const reloadMutation = useMutation({
    mutationFn: () => api.reloadRules(authToken),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
    },
  });

  const handleSelectRule = (id: string) => {
    setCreating(false);
    setSelectedId(id);
  };

  const handleAddNew = () => {
    setSelectedId(null);
    setCreating(true);
  };

  const handleReload = () => {
    reloadMutation.mutate();
  };

  const handleTestRun = (text: string) =>
    api.testRule(text, creating ? undefined : (selectedId ?? undefined), authToken);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">Rules</h1>
        <div className="flex items-center gap-2">
          {reloadMutation.isSuccess && (
            <span className="text-xs text-muted-foreground">
              Reloaded {reloadMutation.data.total_rules} rules
            </span>
          )}
          {reloadMutation.isError && (
            <span className="text-xs text-destructive">Reload failed</span>
          )}
          <Button variant="outline" onClick={handleReload} disabled={reloadMutation.isPending}>
            {reloadMutation.isPending ? "Reloading..." : "Reload from disk"}
          </Button>
          <Button onClick={handleAddNew}>Add new rule</Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>Rules</CardTitle>
          </CardHeader>
          <CardContent>
            <RulesList
              rules={rules}
              selectedId={creating ? null : selectedId}
              onSelect={handleSelectRule}
              loading={rulesQuery.isLoading}
            />
          </CardContent>
        </Card>

        <div className="lg:col-span-2">
          {creating || selectedRule ? (
            <RuleEditor
              value={yamlText}
              onChange={(v) => {
                setYamlText(v);
                setSaveError(null);
              }}
              errors={errors}
              creating={creating}
              version={creating ? undefined : selectedRule?.version}
              sourceFile={creating ? undefined : selectedRule?.source_file}
              sourceFiles={sourceFiles}
              createSourceFile={createSourceFile}
              onCreateSourceFileChange={setCreateSourceFile}
              onSave={() => saveMutation.mutate()}
              onTest={() => setTestOpen(true)}
              saving={saveMutation.isPending}
              saveError={saveError}
            />
          ) : (
            <Card>
              <CardContent className="flex h-64 items-center justify-center text-sm text-muted-foreground">
                Select a rule from the list or click &quot;Add new rule&quot;.
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <TestModal
        open={testOpen}
        onOpenChange={setTestOpen}
        ruleId={creating ? null : selectedId}
        onRun={handleTestRun}
      />
    </div>
  );
}
