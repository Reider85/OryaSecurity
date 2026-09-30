"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

export default function RulesPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Rules</h1>
        <div className="flex gap-2">
          <Button variant="outline">Reload from disk</Button>
          <Button>Add new rule</Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>Rule Files</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              <div className="rounded-md bg-accent p-2 text-sm font-medium">
                pii/
              </div>
              <div className="ml-4 space-y-1">
                <div className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent cursor-pointer">
                  pii_ssn_us
                </div>
                <div className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent cursor-pointer">
                  pii_passport_ru
                </div>
                <div className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent cursor-pointer">
                  pii_email
                </div>
              </div>
              <div className="rounded-md bg-accent p-2 text-sm font-medium">
                secrets/
              </div>
              <div className="ml-4 space-y-1">
                <div className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent cursor-pointer">
                  secrets_aws_key
                </div>
                <div className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent cursor-pointer">
                  secrets_jwt
                </div>
                <div className="rounded-md p-2 text-sm text-muted-foreground hover:bg-accent cursor-pointer">
                  secrets_credit_card
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Rule Editor</CardTitle>
            <Badge variant="secondary">v1.0.0</Badge>
          </CardHeader>
          <CardContent>
            <Textarea
              className="min-h-[400px] font-mono text-sm"
              placeholder="# Select a rule to edit..."
            />
            <div className="mt-4 flex gap-2">
              <Button>Save</Button>
              <Button variant="outline">Test</Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
