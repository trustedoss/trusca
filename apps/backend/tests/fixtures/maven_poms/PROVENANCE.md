# Maven POM fixtures

Unmodified POM files downloaded from Maven Central
(`https://repo1.maven.org/maven2/<group path>/<artifact>/<version>/<artifact>-<version>.pom`)
on 2026-09-20. File names are `<groupId>_<artifactId>_<version>.pom`. The POMs are
published metadata; no file was edited.

They are used by `tests/unit/integrations/license_fetcher/test_maven_parent.py` to
exercise parent-POM licence inheritance against real chains:

| Child (declares no licenses) | Chain read | Ancestor that declares |
|---|---|---|
| `org.slf4j:slf4j-api:2.0.2` | `slf4j-parent:2.0.2` | `slf4j-parent` (MIT License) |
| `org.apache.commons:commons-lang3:3.14.0` | `commons-parent:64`, `org.apache:apache:30` | `apache:30` |
| `com.fasterxml.jackson.datatype:jackson-datatype-jdk8:2.15.3` and two siblings | `jackson-modules-java8:2.15.3`, `jackson-base:2.15.3` | `jackson-base` |
| `ch.qos.logback:logback-core:1.4.11` | `logback-parent:1.4.11` | `logback-parent` |

`org.springframework.boot:spring-boot:3.2.0` declares its own license and is the
case where the parent must not be read. `org.slf4j:jul-to-slf4j:2.0.9` is kept as a
second child of the slf4j project.

The children come from the components of `tests/fixtures/sbom/real_cyclonedx_maven_scoped.json`
that carry no license in the SBOM.
