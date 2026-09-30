from .models import ResourcePreset

PRESETS = {
    "small": ResourcePreset(name="small", vcpu=0.5, memory_mib=1024, cpu_units=512),
    "medium": ResourcePreset(name="medium", vcpu=1, memory_mib=2048, cpu_units=1024),
    "large": ResourcePreset(name="large", vcpu=2, memory_mib=4096, cpu_units=2048),
}
