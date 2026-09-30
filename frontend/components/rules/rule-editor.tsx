"use client";

import { useSyncExternalStore } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { oneDark } from "@codemirror/theme-one-dark";
import { useTheme } from "next-themes";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

// Hydration-safe client detection (avoids setState-in-effect).
const emptySubscribe = () => () => {};
const useIsClient = () =>
  useSyncExternalStore(emptySubscribe, () => true, () => false);

interface RuleEditorProps {
  value: string;
  onChange: (value: string) => void;
  errors: string[];
  creating: boolean;
  version?: string;
  sourceFile?: string;
  sourceFiles: string[];
  createSourceFile: string;
  onCreateSourceFileChange: (file: string) => void;
  onSave: () => void;
  onTest: () => void;
  saving?: boolean;
  saveError?: string | null;
}

export function RuleEditor({
  value,
  onChange,
  errors,
  creating,
  version,
  sourceFile,
  sourceFiles,
  createSourceFile,
  onCreateSourceFileChange,
  onSave,
  onTest,
  saving,
  saveError,
}: RuleEditorProps) {
  const { resolvedTheme } = useTheme();
  const mounted = useIsClient();

  const isValid = errors.length === 0;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2">
          {creating ? "New Rule" : "Rule Editor"}
          {sourceFile && !creating && (
            <Badge variant="outline" className="font-mono text-xs">
              {sourceFile}
            </Badge>
          )}
          {version && <Badge variant="secondary">v{version}</Badge>}
        </CardTitle>
        {creating && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            Target file
            <select
              value={createSourceFile}
              onChange={(e) => onCreateSourceFileChange(e.target.value)}
              className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
            >
              {sourceFiles.map((file) => (
                <option key={file} value={file}>
                  {file}
                </option>
              ))}
              <option value="custom.yaml">custom.yaml (new)</option>
            </select>
          </label>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="overflow-hidden rounded-md border">
          <CodeMirror
            value={value}
            onChange={onChange}
            theme={mounted && resolvedTheme === "dark" ? oneDark : undefined}
            extensions={[yaml()]}
            className="min-h-[420px] text-sm"
            basicSetup={{ lineNumbers: true, foldGutter: true }}
          />
        </div>

        {errors.length > 0 && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3">
            <p className="mb-1 text-sm font-medium text-destructive">Validation errors</p>
            <ul className="list-inside list-disc space-y-0.5 text-sm text-destructive">
              {errors.map((err) => (
                <li key={err}>{err}</li>
              ))}
            </ul>
          </div>
        )}

        {saveError && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {saveError}
          </div>
        )}

        <div className="flex gap-2">
          <Button onClick={onSave} disabled={saving || !isValid}>
            {saving ? "Saving..." : "Save"}
          </Button>
          <Button variant="outline" onClick={onTest} disabled={!isValid}>
            Test
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
