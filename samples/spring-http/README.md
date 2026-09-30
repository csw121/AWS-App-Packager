# Authored validation sample

Java 21, Spring Boot 3.5.6, Maven single module. No database, login, storage, or external API.
Port 8080; GET `/health` returns status UP and marker `packager-spring-v1`.
Built only inside the pinned Paketo builder; do not run host Maven/JDK.
Maven and JVM dependencies require download. HTTP checks do not establish AWS compatibility.
