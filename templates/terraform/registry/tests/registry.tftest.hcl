# Explicit provider mock: this test never authenticates or calls AWS.
mock_provider "aws" {}
run "immutable_dedicated_repository" {
  command = plan
  variables {
    aws_region      = "ap-northeast-2"
    repository_name = "packager-test-only"
  }
  assert {
    condition     = aws_ecr_repository.app.image_tag_mutability == "IMMUTABLE" && aws_ecr_repository.app.force_delete == false
    error_message = "Repository must be immutable and must not force-delete images."
  }
  assert {
    condition     = one(aws_ecr_repository.app.encryption_configuration).encryption_type == "AES256" && one(aws_ecr_repository.app.image_scanning_configuration).scan_on_push
    error_message = "Repository encryption and scanning settings changed."
  }
}
