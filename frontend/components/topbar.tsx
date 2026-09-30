"use client";

import { LogOut, User } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useRouter } from "next/navigation";

export function Topbar() {
  const { logout } = useAuth();
  const router = useRouter();

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-4 border-b bg-background px-6">
      <div className="flex-1" />
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 rounded-md border px-3 py-1.5">
          <User className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">Admin</span>
        </div>
        <ThemeToggle />
        <Button variant="ghost" size="icon" onClick={handleLogout} title="Logout">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
