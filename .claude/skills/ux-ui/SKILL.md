---
name: ux-ui
description: Aplica principios de UX/UI y mejores practicas de diseno para interfaces web. Usa este skill cuando el usuario pida mejorar la interfaz, revisar UX, o crear componentes visuales.
allowed-tools: Read, Edit, Write, Glob, Grep
---

# Skill de UX/UI - Guia de Mejores Practicas

Cuando trabajes en interfaces de usuario, aplica estos principios y verifica el checklist.

---

## 1. PRINCIPIOS FUNDAMENTALES

### Jerarquia Visual
- Tamanos de fuente: establecer escala clara (ej: 12, 14, 16, 20, 24, 32px)
- Peso de fuente: usar bold para titulos, regular para texto
- Color: elementos importantes con mayor contraste
- Espaciado: mas espacio alrededor de elementos importantes

### Consistencia
- Espaciado uniforme (usar multiplos de 4px u 8px)
- Colores consistentes para acciones similares
- Tipografia uniforme en toda la aplicacion
- Patrones de interaccion predecibles

### Feedback Visual
- Estados hover en elementos interactivos
- Estados focus para accesibilidad
- Estados loading/disabled claros
- Confirmacion de acciones completadas
- Mensajes de error descriptivos

### Accesibilidad (A11y)
- Contraste minimo 4.5:1 para texto normal
- Contraste minimo 3:1 para texto grande (18px+)
- Elementos interactivos minimo 44x44px en mobile
- Labels en todos los inputs
- Alt text en imagenes
- Navegacion por teclado funcional

---

## 2. PALETA DE COLORES RECOMENDADA

### Estructura
```
- Primary: Color principal de marca (botones, links, CTAs)
- Secondary: Color complementario
- Success: Verde (#22c55e o similar)
- Warning: Amarillo/Naranja (#f59e0b o similar)
- Error: Rojo (#ef4444 o similar)
- Neutral: Grises para texto y fondos
```

### Dark Mode
```css
--bg-primary: #0f172a;      /* Fondo principal */
--bg-secondary: #1e293b;    /* Tarjetas, modales */
--bg-tertiary: #334155;     /* Inputs, hovers */
--text-primary: #f8fafc;    /* Texto principal */
--text-secondary: #94a3b8;  /* Texto secundario */
--text-muted: #64748b;      /* Texto deshabilitado */
--border: #475569;          /* Bordes */
```

### Light Mode
```css
--bg-primary: #ffffff;
--bg-secondary: #f8fafc;
--bg-tertiary: #f1f5f9;
--text-primary: #0f172a;
--text-secondary: #475569;
--text-muted: #94a3b8;
--border: #e2e8f0;
```

---

## 3. TIPOGRAFIA

### Escala Recomendada
```css
--text-xs: 0.75rem;    /* 12px - labels pequenos */
--text-sm: 0.875rem;   /* 14px - texto secundario */
--text-base: 1rem;     /* 16px - texto principal */
--text-lg: 1.125rem;   /* 18px - subtitulos */
--text-xl: 1.25rem;    /* 20px - titulos seccion */
--text-2xl: 1.5rem;    /* 24px - titulos pagina */
--text-3xl: 1.875rem;  /* 30px - heroes */
--text-4xl: 2.25rem;   /* 36px - heroes grandes */
```

### Line Height
- Titulos: 1.2 - 1.3
- Texto cuerpo: 1.5 - 1.6
- UI compacta: 1.25

### Fuentes Recomendadas
- Sistema: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif
- Monospace: 'Fira Code', 'JetBrains Mono', Consolas, monospace

---

## 4. ESPACIADO

### Sistema de 4px
```css
--space-1: 0.25rem;   /* 4px */
--space-2: 0.5rem;    /* 8px */
--space-3: 0.75rem;   /* 12px */
--space-4: 1rem;      /* 16px */
--space-5: 1.25rem;   /* 20px */
--space-6: 1.5rem;    /* 24px */
--space-8: 2rem;      /* 32px */
--space-10: 2.5rem;   /* 40px */
--space-12: 3rem;     /* 48px */
--space-16: 4rem;     /* 64px */
```

### Uso
- Entre elementos relacionados: 8-16px
- Entre secciones: 24-48px
- Padding de contenedores: 16-24px
- Padding de botones: 8-12px vertical, 16-24px horizontal

---

## 5. COMPONENTES

### Botones
```css
/* Primario */
.btn-primary {
    background: var(--primary);
    color: white;
    padding: 10px 20px;
    border-radius: 8px;
    font-weight: 600;
    transition: all 0.2s;
}
.btn-primary:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

/* Tamanos */
.btn-sm { padding: 6px 12px; font-size: 14px; }
.btn-md { padding: 10px 20px; font-size: 16px; }
.btn-lg { padding: 14px 28px; font-size: 18px; }
```

### Inputs
```css
.input {
    width: 100%;
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 16px; /* Previene zoom en iOS */
    transition: border-color 0.2s, box-shadow 0.2s;
}
.input:focus {
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px rgba(primary, 0.1);
}
.input:invalid {
    border-color: var(--error);
}
```

### Tarjetas
```css
.card {
    background: var(--bg-secondary);
    border-radius: 12px;
    padding: 20px;
    border: 1px solid var(--border);
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.card:hover {
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}
```

---

## 6. RESPONSIVE DESIGN

### Breakpoints
```css
/* Mobile first */
@media (min-width: 640px) { /* sm - Tablet */ }
@media (min-width: 768px) { /* md - Tablet landscape */ }
@media (min-width: 1024px) { /* lg - Desktop */ }
@media (min-width: 1280px) { /* xl - Desktop grande */ }
```

### Consideraciones Mobile
- Touch targets minimo 44x44px
- Font-size minimo 16px en inputs (evita zoom)
- Navegacion accesible con una mano
- Contenido importante "above the fold"
- Evitar hover como unico indicador

---

## 7. ANIMACIONES

### Duraciones
```css
--duration-fast: 150ms;    /* Hovers, toggles */
--duration-normal: 200ms;  /* Transiciones UI */
--duration-slow: 300ms;    /* Modales, drawers */
--duration-slower: 500ms;  /* Animaciones complejas */
```

### Easing
```css
--ease-out: cubic-bezier(0, 0, 0.2, 1);      /* Entradas */
--ease-in: cubic-bezier(0.4, 0, 1, 1);       /* Salidas */
--ease-in-out: cubic-bezier(0.4, 0, 0.2, 1); /* Movimientos */
```

### Reducir movimiento
```css
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        transition-duration: 0.01ms !important;
    }
}
```

---

## 8. CHECKLIST DE REVISION UX/UI

### Visual
- [ ] Jerarquia visual clara
- [ ] Espaciado consistente
- [ ] Colores con suficiente contraste
- [ ] Tipografia legible
- [ ] Iconos claros y consistentes

### Interaccion
- [ ] Estados hover/focus visibles
- [ ] Feedback inmediato en acciones
- [ ] Loading states claros
- [ ] Mensajes de error utiles
- [ ] Confirmacion de acciones destructivas

### Accesibilidad
- [ ] Navegable por teclado
- [ ] Labels en formularios
- [ ] Alt text en imagenes
- [ ] Contraste suficiente
- [ ] Focus visible

### Responsive
- [ ] Funciona en mobile (320px+)
- [ ] Touch targets adecuados
- [ ] Texto legible sin zoom
- [ ] Layout adaptable

### Performance
- [ ] Imagenes optimizadas
- [ ] Lazy loading donde aplique
- [ ] Animaciones suaves (60fps)
- [ ] Sin layout shifts

---

## 9. PATRONES COMUNES

### Empty States
- Ilustracion o icono
- Titulo descriptivo
- Descripcion breve
- CTA para siguiente accion

### Loading States
- Skeleton screens para contenido
- Spinners para acciones
- Progress bars para procesos largos
- Indicar tiempo estimado si es largo

### Error States
- Color rojo para errores
- Icono de alerta
- Mensaje claro del problema
- Sugerencia de solucion
- Accion para reintentar

### Success States
- Color verde
- Icono de check
- Confirmacion clara
- Siguiente paso o cierre automatico

---

## 10. CUANDO APLICAR ESTE SKILL

Usa estos principios cuando:
1. Crees nuevos componentes UI
2. Revises interfaces existentes
3. El usuario pida mejorar UX/UI
4. Detectes problemas de usabilidad
5. Implementes responsive design
6. Trabajes con formularios
7. Agregues animaciones/transiciones

Siempre prioriza:
1. Funcionalidad sobre estetica
2. Claridad sobre creatividad
3. Consistencia sobre novedad
4. Accesibilidad sobre tendencias
