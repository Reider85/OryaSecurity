"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function ConfigPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Configuration</h1>
        <div className="flex gap-2">
          <Button variant="outline">Reset to defaults</Button>
          <Button>Save</Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>LLM Provider</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Provider URL</label>
              <Input defaultValue="http://localhost:8001" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Default Model</label>
              <Input defaultValue="gpt-4" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Timeout (seconds)</label>
              <Input type="number" defaultValue={30} />
            </div>
            <Button variant="outline">Test Connection</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Auth</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Default API Key</label>
              <Input type="password" defaultValue="••••••••" />
            </div>
            <Button variant="outline">Generate New Key</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Cache</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">TTL (minutes)</label>
              <Input type="number" defaultValue={5} />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Max Size</label>
              <Input type="number" defaultValue={10000} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Scanner</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Max Prompt Length (chars)</label>
              <Input type="number" defaultValue={10000} />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Rate Limit (RPS per key)</label>
              <Input type="number" defaultValue={100} />
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
