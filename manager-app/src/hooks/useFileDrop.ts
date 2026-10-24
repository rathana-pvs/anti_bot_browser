import { useRef, useState, type DragEvent } from 'react';

/** Keep nested children from flickering the drop highlight as the pointer moves. */
export function useFileDrop(onFiles: (files: File[]) => void, disabled = false) {
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const handlers = {
    onDragEnter(event: DragEvent<HTMLElement>) {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      event.stopPropagation();
      depth.current += 1;
      if (!disabled) setDragging(true);
    },
    onDragOver(event: DragEvent<HTMLElement>) {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = disabled ? 'none' : 'copy';
    },
    onDragLeave(event: DragEvent<HTMLElement>) {
      event.preventDefault();
      event.stopPropagation();
      depth.current = Math.max(0, depth.current - 1);
      if (!depth.current) setDragging(false);
    },
    onDrop(event: DragEvent<HTMLElement>) {
      event.preventDefault();
      event.stopPropagation();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(event.dataTransfer.files);
      if (!disabled && files.length) onFiles(files);
    },
  };
  return { dragging: dragging && !disabled, handlers };
}
