# -*- coding: utf-8 -*-
"""
Herramientas de control de flujo para proteger APIs remotas.
"""
import time
import threading
from datetime import datetime, timedelta
from collections import deque


class RateLimiter:
    """
    Limitador de tasa de requests para evitar saturar el servidor remoto.
    
    Uso:
        limiter = RateLimiter(max_requests=5, time_window=1.0)
        with limiter:
            # Tu código que hace request
            pass
    """
    
    def __init__(self, max_requests=5, time_window=1.0):
        """
        Args:
            max_requests: Número máximo de requests permitidos
            time_window: Ventana de tiempo en segundos
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()
        self.lock = threading.Lock()
    
    def __enter__(self):
        """Context manager entry."""
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        pass
    
    def acquire(self):
        """Espera si es necesario antes de permitir el request."""
        with self.lock:
            now = time.time()
            
            # Limpiar requests antiguos fuera de la ventana
            while self.requests and self.requests[0] < now - self.time_window:
                self.requests.popleft()
            
            # Si estamos al límite, esperar
            if len(self.requests) >= self.max_requests:
                # Calcular cuánto tiempo esperar
                oldest_request = self.requests[0]
                sleep_time = self.time_window - (now - oldest_request) + 0.01
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    # Limpiar nuevamente después de dormir
                    now = time.time()
                    while self.requests and self.requests[0] < now - self.time_window:
                        self.requests.popleft()
            
            # Registrar este request
            self.requests.append(now)


class CircuitBreaker:
    """
    Circuit Breaker para proteger contra cascadas de fallos.
    
    Estados:
    - CLOSED: Funcionamiento normal
    - OPEN: Demasiados fallos, bloquear llamadas
    - HALF_OPEN: Intentando recuperarse
    
    Uso:
        breaker = CircuitBreaker(failure_threshold=5, timeout_duration=300)
        
        try:
            with breaker:
                result = call_remote_api()
        except CircuitOpenError:
            # El circuito está abierto, el remoto está caído
            pass
    """
    
    STATE_CLOSED = 'closed'
    STATE_OPEN = 'open'
    STATE_HALF_OPEN = 'half_open'
    
    def __init__(self, failure_threshold=5, timeout_duration=300, success_threshold=2):
        """
        Args:
            failure_threshold: Número de fallos consecutivos antes de abrir
            timeout_duration: Segundos antes de intentar recuperar (pasar a HALF_OPEN)
            success_threshold: Éxitos consecutivos en HALF_OPEN para cerrar
        """
        self.failure_threshold = failure_threshold
        self.timeout_duration = timeout_duration
        self.success_threshold = success_threshold
        
        self.state = self.STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.lock = threading.Lock()
    
    def __enter__(self):
        """Context manager entry."""
        self.call()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if exc_type is None:
            # Éxito
            self.on_success()
        else:
            # Fallo
            self.on_failure()
        return False  # No suprimir excepciones
    
    def call(self):
        """Verifica si se puede hacer la llamada."""
        with self.lock:
            if self.state == self.STATE_OPEN:
                # Verificar si es tiempo de intentar recuperar
                if self._should_attempt_reset():
                    self.state = self.STATE_HALF_OPEN
                    self.success_count = 0
                else:
                    raise CircuitOpenError(
                        f"Circuit breaker está OPEN. "
                        f"Último fallo hace {self._seconds_since_last_failure():.1f}s. "
                        f"Reintentará en {self._seconds_until_retry():.1f}s."
                    )
    
    def on_success(self):
        """Registra un éxito."""
        with self.lock:
            self.failure_count = 0
            
            if self.state == self.STATE_HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    # Recuperado exitosamente
                    self.state = self.STATE_CLOSED
                    self.success_count = 0
    
    def on_failure(self):
        """Registra un fallo."""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            
            if self.state == self.STATE_HALF_OPEN:
                # Falló durante recuperación, volver a OPEN
                self.state = self.STATE_OPEN
                self.success_count = 0
            elif self.failure_count >= self.failure_threshold:
                # Demasiados fallos, abrir circuito
                self.state = self.STATE_OPEN
    
    def _should_attempt_reset(self):
        """Verifica si es tiempo de intentar recuperar."""
        if self.last_failure_time is None:
            return True
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.timeout_duration
    
    def _seconds_since_last_failure(self):
        """Segundos desde el último fallo."""
        if self.last_failure_time is None:
            return 0
        return (datetime.now() - self.last_failure_time).total_seconds()
    
    def _seconds_until_retry(self):
        """Segundos hasta el próximo intento."""
        if self.last_failure_time is None:
            return 0
        elapsed = self._seconds_since_last_failure()
        remaining = max(0, self.timeout_duration - elapsed)
        return remaining
    
    def get_state(self):
        """Retorna el estado actual del circuit breaker."""
        return {
            'state': self.state,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'last_failure_time': self.last_failure_time,
        }
    
    def reset(self):
        """Resetea manualmente el circuit breaker."""
        with self.lock:
            self.state = self.STATE_CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None


class CircuitOpenError(Exception):
    """Excepción lanzada cuando el circuit breaker está abierto."""
    pass
