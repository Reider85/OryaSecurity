"use client";

import { SelectHTMLAttributes, useState } from "react";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: Array<{ value: string; label: string }>;
  placeholder?: string;
}

export function Select({ options, placeholder, value, onChange, className, ...props }: SelectProps) {
  const [selectedValue, setSelectedValue] = useState(value || "");

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newValue = e.target.value;
    setSelectedValue(newValue);
    onChange?.(e);
  };

  return (
    <select
      value={selectedValue}
      onChange={handleChange}
      className={`px-3 py-2 border border-input rounded-md text-sm ${className}`}
      {...props}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}