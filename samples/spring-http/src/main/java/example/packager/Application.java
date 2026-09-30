package example.packager;

import java.util.Map;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@SpringBootApplication
@RestController
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }

    @GetMapping("/")
    public Map<String, String> index() {
        return Map.of("app", "AWS App Packager sample", "runtime", "Java 21");
    }

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "UP", "marker", "packager-spring-v1");
    }
}
