"""Validador de órdenes. PUNTO DE CONTROL OBLIGATORIO para toda orden antes de llegar a execution.

REGLA: Ninguna orden pasa a bot/execution/ sin haber sido aprobada por este módulo.
Verifica: SL presente, sizing dentro de límites, spread aceptable, drawdown no superado,
kill switch de balance, bloqueo por eventos económicos de alto impacto.
"""
